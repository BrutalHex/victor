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
	"sync"
	"syscall"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/anki"
	"github.com/BrutalHex/victor/robot/agent/internal/blemask"
	"github.com/BrutalHex/victor/robot/agent/internal/cliffcal"
	"github.com/BrutalHex/victor/robot/agent/internal/face"
	"github.com/BrutalHex/victor/robot/agent/internal/latch"
	"github.com/BrutalHex/victor/robot/agent/internal/link"
	"github.com/BrutalHex/victor/robot/agent/internal/simulate"
	"github.com/BrutalHex/victor/robot/agent/internal/skill"
	"github.com/BrutalHex/victor/robot/agent/internal/spine"
	"github.com/BrutalHex/victor/robot/agent/internal/sshctl"
	"github.com/BrutalHex/victor/robot/agent/internal/telem"
	"github.com/BrutalHex/victor/robot/agent/internal/vct1"
	"github.com/BrutalHex/victor/robot/agent/internal/veto"
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
		case "mask-anki":
			if err := anki.Mask(); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			fmt.Println("anki-robot.target stopped")
			return
		case "restore-anki":
			if err := anki.Restore(); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			fmt.Println("anki-robot.target started")
			return
		case "calibrate":
			_ = os.Remove(cliffcal.Path)
			fmt.Println("cliff calibration cleared; agent will recapture floor samples")
			return
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
	showFace(on, false)
	fmt.Printf("CHARGE-LATCH simulated ssh.enabled=%v\n", on)
	return 0
}

func printStatus() {
	c := sshctl.New()
	fmt.Printf("ssh.enabled=%v\n", c.Enabled())
	fmt.Println(blemask.StatusLine())
	fmt.Printf("anki.masked=%v engine=%v\n", anki.Masked(), anki.EngineActive())
	fmt.Printf("openai_key_on_robot=%v\n", openaiPresent())
	if b, err := os.ReadFile("/data/victor/veto.txt"); err == nil {
		fmt.Printf("veto=%s", b)
	}
	if b, err := os.ReadFile("/data/victor/skill.txt"); err == nil {
		fmt.Printf("skill=%s", b)
	}
	if b, err := os.ReadFile("/data/victor/motors.txt"); err == nil {
		fmt.Printf("motors=%s", b)
	}
	fmt.Printf("explore.enabled=%v\n", skill.ExploreEnabled())
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
		hubHost   = flag.String("hub", env("HUB_HOST", "robot.mohammadabbasi.com"), "hub hostname or IP")
		hubPort   = flag.Int("sensor-port", 7502, "UDP SENSOR port")
		grpcPort  = flag.Int("grpc-port", 7443, "TCP heartbeat/gRPC port")
		inject    = flag.String("inject", "/run/victor/inject", "unix socket for CHARGE-LATCH samples")
		logPath   = flag.String("telem-log", "/data/victor/telemetry.log", "local telemetry log")
		rate      = flag.Duration("period", 20*time.Millisecond, "control/sensor period")
		ownSpine  = flag.Bool("own-spine", anki.Masked(), "stop Anki and take /dev/ttyHS0")
		spineDev  = flag.String("spine", spine.DefaultDevice, "spine UART")
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

	var body *spine.Body
	if *ownSpine {
		if err := anki.Mask(); err != nil {
			fmt.Fprintf(os.Stderr, "mask anki: %v\n", err)
		}
		time.Sleep(400 * time.Millisecond)
		b, err := spine.Open(*spineDev)
		if err != nil {
			fmt.Fprintf(os.Stderr, "spine open: %v (SENSOR will be empty)\n", err)
		} else {
			body = b
			defer body.Close()
			body.SetSSHLED(ssh.Enabled())
			fmt.Println("spine owned, motors held at 0")
		}
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

	hbHost := env("HUB_HOST", *hubHost)
	lnk := &link.Client{Addr: fmt.Sprintf("%s:%d", hbHost, *grpcPort)}
	defer lnk.Close()

	cal, _ := cliffcal.Load(cliffcal.Path)
	if cal == nil {
		cal = &cliffcal.Cal{}
	}

	ov := &overlay{}
	m := latch.New()
	go injectLoop(*inject, m, ssh, body, ov)
	go watchdog(ssh, hub, body)
	face.EOK()

	tick := time.NewTicker(*rate)
	defer tick.Stop()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	start := time.Now()
	gotSpine := false
	var lastCmd time.Time
	var lastKind skill.Kind
	var lastReason veto.Reason
	wroteCal := cal.Ready()

	for {
		select {
		case <-sig:
			return 0
		case <-tick.C:
			if body != nil {
				if err := body.Pump(); err != nil {
					fmt.Fprintf(os.Stderr, "spine: %v\n", err)
				}
			}
			s := vct1.Sensor{BattMV: 3900}
			fr, have := spine.Frame{}, false
			if body != nil {
				fr, have = body.Last()
			}
			if have {
				gotSpine = true
				s = fr.Sensor()
				if !fr.Driving() && !cal.Ready() {
					cal.Add(fr.Cliffs)
					if cal.Ready() && !wroteCal {
						_ = cal.Save(cliffcal.Path)
						wroteCal = true
						fmt.Printf("cliffs calibrated floor=%v thresh=%v\n", cal.Floor, cal.Thresh)
					}
				}
				lo, hi := body.LiftRange()
				if m.Feed(latch.Sample{
					T:         time.Since(start),
					Button:    fr.Button,
					OnCharger: fr.OnCharger(),
					Driving:   fr.Driving(),
					LiftNorm:  fr.LiftNorm(lo, hi),
				}) == latch.ToggleSSH {
					on, err := ssh.Toggle()
					fmt.Printf("CHARGE-LATCH ssh.enabled=%v err=%v\n", on, err)
					showFace(on, false)
					body.SetSSHLED(on)
				}
			}
			cliffs := s.Cliffs
			if c := ov.cliffs(); c != nil {
				cliffs = *c
			}
			if b, err := os.ReadFile("/data/victor/force-cliffs"); err == nil {
				var a, b2, c, d int
				if n, _ := fmt.Sscanf(string(b), "%d,%d,%d,%d", &a, &b2, &c, &d); n == 4 {
					cliffs = [4]uint16{uint16(a), uint16(b2), uint16(c), uint16(d)}
				}
			}
			kind := lnk.Skill()
			if k, ok := ov.skill(); ok {
				kind = k
				lastCmd = time.Now()
			}
			if b, err := os.ReadFile("/data/victor/force-skill"); err == nil {
				kind = skill.Parse(strings.TrimSpace(string(b)))
				lastCmd = time.Now()
			}
			if kind != skill.Idle && kind != skill.Stop {
				lastCmd = time.Now()
			}
			lastKind = kind

			vin := veto.Input{
				Cliffs:       cliffs,
				Thresh:       cal.Thresh,
				Calibrated:   cal.Ready(),
				ProxMM:       s.ProxMM,
				Forward:      kind.Forward(),
				OnCharger:    have && fr.OnCharger(),
				PickedUp:     false,
				Falling:      false,
				HasHeartbeat: !lnk.LastOK().IsZero(),
				BattMV:       s.BattMV,
				HasCommand:   lastKind != skill.Idle && lastKind != skill.Stop && lastKind != skill.Dock,
			}
			if vin.HasHeartbeat {
				vin.HeartbeatAge = time.Since(lnk.LastOK())
			}
			if vin.HasCommand {
				vin.CommandAge = time.Since(lastCmd)
			}
			reason := veto.Check(vin)
			allow := skill.AllowWheels(skill.ExploreEnabled(), reason == veto.Clear, vin.OnCharger)
			pwm := skill.PWM(kind, allow)
			if reason != veto.Clear {
				pwm = [4]int16{}
			}
			if body != nil {
				body.SetDrive(pwm)
			}
			if reason != lastReason {
				lastReason = reason
				if reason == veto.Cliff || reason == veto.Pickup || reason == veto.Fall || reason == veto.Battery {
					face.Show(strings.ToUpper(reason.String()), face.Red)
				} else if reason == veto.Clear {
					face.EOK()
				}
			}
			_ = os.WriteFile("/data/victor/veto.txt", []byte(reason.String()+"\n"), 0644)
			ageMs := int64(0)
			if vin.HasHeartbeat {
				ageMs = vin.HeartbeatAge.Milliseconds()
			}
			_ = os.WriteFile("/data/victor/hb_age_ms.txt", []byte(fmt.Sprintf("%d\n", ageMs)), 0644)
			_ = os.WriteFile("/data/victor/skill.txt", []byte(kind.String()+"\n"), 0644)
			_ = os.WriteFile("/data/victor/motors.txt", []byte(fmt.Sprintf("%d,%d,%d,%d\n", pwm[0], pwm[1], pwm[2], pwm[3])), 0644)
			if _, err := os.Stat("/data/victor/hub.down"); err == nil {
				lnk.Close()
			} else {
				if !lnk.LastOK().IsZero() {
					hub.NoteOK()
				}
				lnk.Tick(uint8(reason))
			}
			_ = hub.SendSensor(s)
			if gotSpine {
				_ = os.WriteFile("/data/victor/spine.ok", []byte("1\n"), 0644)
			}
		}
	}
}

type overlay struct {
	mu     sync.Mutex
	cl     *[4]uint16
	sk     *skill.Kind
}

func (o *overlay) cliffs() *[4]uint16 {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.cl
}

func (o *overlay) skill() (skill.Kind, bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.sk == nil {
		return 0, false
	}
	return *o.sk, true
}

type inj struct {
	Button    *bool     `json:"button"`
	OnCharger *bool     `json:"on_charger"`
	Driving   *bool     `json:"driving"`
	Lift      *float64  `json:"lift"`
	Phrase    string    `json:"phrase"`
	Cliffs    *[4]uint16 `json:"cliffs"`
	Skill     string    `json:"skill"`
}

func injectLoop(path string, m *latch.Machine, ssh *sshctl.Controller, body *spine.Body, ov *overlay) {
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
				if s.Cliffs != nil && ov != nil {
					ov.mu.Lock()
					ov.cl = s.Cliffs
					ov.mu.Unlock()
					fmt.Fprintf(conn, "cliffs override %v\n", *s.Cliffs)
					continue
				}
				if s.Skill != "" && ov != nil {
					k := skill.Parse(s.Skill)
					ov.mu.Lock()
					ov.sk = &k
					ov.mu.Unlock()
					fmt.Fprintf(conn, "skill %s\n", k)
					continue
				}
				if s.Phrase == "toggle" || s.Phrase == "charge-latch" {
					if simulate.ChargeLatch(m) == latch.ToggleSSH {
						on, err := ssh.Toggle()
						fmt.Fprintf(conn, "toggle ssh.enabled=%v err=%v\n", on, err)
						showFace(on, false)
						if body != nil {
							body.SetSSHLED(on)
						}
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
					showFace(on, false)
					if body != nil {
						body.SetSSHLED(on)
					}
				} else {
					fmt.Fprintf(conn, "ok phase=%s\n", m.Phase())
				}
			}
		}(c)
	}
}

func watchdog(ssh *sshctl.Controller, hub *telem.Hub, body *spine.Body) {
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
			showFace(true, true)
			if body != nil {
				body.SetSSHLED(true)
			}
		}
	}
}

func showFace(sshOn, auto bool) {
	text := "SSH OFF"
	fg := face.Red
	if auto {
		text = "SSH AUTO"
		fg = face.Amber
	} else if sshOn {
		text = "SSH ON"
		fg = face.Green
	}
	face.Show(text, fg)
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


