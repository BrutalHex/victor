package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/blemask"
	"github.com/BrutalHex/victor/robot/agent/internal/face"
	"github.com/BrutalHex/victor/robot/agent/internal/latch"
	"github.com/BrutalHex/victor/robot/agent/internal/simulate"
	"github.com/BrutalHex/victor/robot/agent/internal/sshctl"
	"github.com/BrutalHex/victor/robot/agent/internal/telem"
	"github.com/BrutalHex/victor/robot/agent/internal/vct1"
)

func main() {
	if len(os.Args) > 1 && !strings.HasPrefix(os.Args[1], "-") {
		switch os.Args[1] {
		case "ssh-on":
			os.Exit(runSSH(true))
		case "ssh-off":
			os.Exit(runSSH(false))
		case "ssh-toggle":
			os.Exit(runToggle())
		case "ble-mask":
			if err := blemask.Apply(); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			fmt.Println(blemask.StatusLine())
			return
		case "status":
			printStatus()
			return
		case "latch-simulate":
			os.Exit(runSimulate())
		case "run":
			os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
		}
	}
	os.Exit(runDaemon())
}

func runSSH(on bool) int {
	c := sshctl.New()
	if err := c.Set(on); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	fmt.Printf("ssh.enabled=%v\n", c.Enabled())
	return 0
}

func runToggle() int {
	c := sshctl.New()
	on, err := c.Toggle()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	fmt.Printf("ssh.enabled=%v\n", on)
	return 0
}

func runSimulate() int {
	m := latch.New()
	if simulate.ChargeLatch(m) != latch.ToggleSSH {
		fmt.Fprintln(os.Stderr, "simulate: FSM did not toggle")
		return 1
	}
	c := sshctl.New()
	on, err := c.Toggle()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	showFace(on, false, "")
	fmt.Printf("CHARGE-LATCH simulated ssh.enabled=%v\n", on)
	return 0
}

func printStatus() {
	c := sshctl.New()
	fmt.Printf("ssh.enabled=%v\n", c.Enabled())
	fmt.Println(blemask.StatusLine())
	fmt.Printf("openai_key_on_robot=%v\n", openaiPresent())
}

func openaiPresent() bool {
	paths := []string{
		"/data/victor/.env",
		"/data/openai.key",
		"/anki/etc/openai",
	}
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err == nil && strings.Contains(strings.ToLower(string(b)), "sk-") {
			return true
		}
	}
	return os.Getenv("OPENAI_API_KEY") != ""
}

func runDaemon() int {
	var (
		hubHost    = flag.String("hub", env("HUB_HOST", "robot.mohammadabbasi.com"), "hub hostname or IP")
		hubPort    = flag.Int("sensor-port", 7502, "UDP SENSOR port")
		grpcPort   = flag.Int("grpc-port", 7443, "TCP heartbeat/gRPC port")
		inject     = flag.String("inject", "/run/victor/inject", "unix socket for CHARGE-LATCH samples")
		faceDev    = flag.String("face", env("FACE_DEV", ""), "optional raw RGB565 device")
		logPath    = flag.String("telem-log", "/data/victor/telemetry.log", "local telemetry log")
		rate       = flag.Duration("sensor-hz", 20*time.Millisecond, "sensor emit period")
	)
	flag.Parse()
	_ = os.MkdirAll("/data/victor", 0755)
	_ = os.MkdirAll("/run/victor", 0755)
	loadHubEnv()

	ssh := sshctl.New()
	if err := ssh.Apply(); err != nil {
		fmt.Fprintf(os.Stderr, "ssh apply: %v\n", err)
	}
	if blemask.Disabled() {
		_ = blemask.Apply()
	}

	hub := &telem.Hub{
		Host:       *hubHost,
		SensorPort: *hubPort,
		GRPCPort:   *grpcPort,
		LogPath:    *logPath,
	}
	if err := hub.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "hub udp: %v (continuing with local log)\n", err)
	}

	m := latch.New()
	go injectLoop(*inject, m, ssh, *faceDev)
	go watchdog(ssh, hub, *faceDev)

	tick := time.NewTicker(*rate)
	defer tick.Stop()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)

	var seq int
	onCharger := false
	button := false
	lift := 0.0
	for {
		select {
		case <-sig:
			return 0
		case <-tick.C:
			seq++
			s := vct1.Sensor{
				EncLift:   int32(lift * 900),
				BattMV:    3900,
				ChargerMV: 0,
				Flags:     0,
			}
			if onCharger {
				s.ChargerMV = 5000
				s.Flags |= vct1.FlagOnCharger
			}
			if button {
				s.Flags |= vct1.FlagButton
			}
			_ = hub.SendSensor(s)
		}
	}
}

type inj struct {
	Button    *bool    `json:"button"`
	OnCharger *bool    `json:"on_charger"`
	Driving   *bool    `json:"driving"`
	Lift      *float64 `json:"lift"`
	Phrase    string   `json:"phrase"`
}

func injectLoop(path string, m *latch.Machine, ssh *sshctl.Controller, faceDev string) {
	_ = os.Remove(path)
	ln, err := net.Listen("unix", path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "inject listen: %v\n", err)
		return
	}
	_ = os.Chmod(path, 0666)
	start := time.Now()
	for {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		go func(conn net.Conn) {
			defer conn.Close()
			sc := bufio.NewScanner(conn)
			for sc.Scan() {
				var s inj
				if err := json.Unmarshal(sc.Bytes(), &s); err != nil {
					fmt.Fprintf(conn, "err %v\n", err)
					continue
				}
				if s.Phrase == "toggle" || s.Phrase == "charge-latch" {
					if simulate.ChargeLatch(m) == latch.ToggleSSH {
						on, err := ssh.Toggle()
						fmt.Fprintf(conn, "toggle ssh.enabled=%v err=%v\n", on, err)
						showFace(on, false, faceDev)
					} else {
						fmt.Fprintf(conn, "fsm miss\n")
					}
					continue
				}
				sample := latch.Sample{
					T:         time.Since(start),
					OnCharger: true,
				}
				if s.Button != nil {
					sample.Button = *s.Button
				}
				if s.OnCharger != nil {
					sample.OnCharger = *s.OnCharger
				}
				if s.Driving != nil {
					sample.Driving = *s.Driving
				}
				if s.Lift != nil {
					sample.LiftNorm = *s.Lift
				}
				if m.Feed(sample) == latch.ToggleSSH {
					on, err := ssh.Toggle()
					fmt.Fprintf(conn, "toggle ssh.enabled=%v err=%v\n", on, err)
					showFace(on, false, faceDev)
				} else {
					fmt.Fprintf(conn, "ok phase=%s\n", m.Phase())
				}
			}
		}(c)
	}
}

func watchdog(ssh *sshctl.Controller, hub *telem.Hub, faceDev string) {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	offSince := time.Time{}
	for range t.C {
		if ssh.Enabled() {
			offSince = time.Time{}
			continue
		}
		if offSince.IsZero() {
			offSince = time.Now()
		}
		if time.Since(offSince) >= 24*time.Hour && hub.HeartbeatMissing(10*time.Minute) {
			_ = ssh.Set(true)
			showFace(true, true, faceDev)
		}
	}
}

func showFace(sshOn, auto bool, faceDev string) {
	text := "SSH OFF"
	fg := face.Red
	if auto {
		text = "SSH AUTO"
		fg = face.Amber
	} else if sshOn {
		text = "SSH ON"
		fg = face.Green
	}
	frame := face.Frame(text, fg)
	_ = os.WriteFile("/data/victor/face.rgb565", frame, 0644)
	_ = face.WriteDev(faceDev, frame)
	fmt.Printf("face %s\n", text)
}

func loadHubEnv() {
	b, err := os.ReadFile("/data/victor/hub.env")
	if err != nil {
		return
	}
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		_ = os.Setenv(strings.TrimSpace(k), strings.TrimSpace(v))
	}
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}


