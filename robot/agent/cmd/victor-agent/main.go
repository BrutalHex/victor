package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/anki"
	"github.com/BrutalHex/victor/robot/agent/internal/audio"
	"github.com/BrutalHex/victor/robot/agent/internal/blemask"
	"github.com/BrutalHex/victor/robot/agent/internal/camera"
	"github.com/BrutalHex/victor/robot/agent/internal/cliffcal"
	"github.com/BrutalHex/victor/robot/agent/internal/face"
	"github.com/BrutalHex/victor/robot/agent/internal/hosts"
	"github.com/BrutalHex/victor/robot/agent/internal/latch"
	"github.com/BrutalHex/victor/robot/agent/internal/link"
	"github.com/BrutalHex/victor/robot/agent/internal/rotlog"
	"github.com/BrutalHex/victor/robot/agent/internal/simulate"
	"github.com/BrutalHex/victor/robot/agent/internal/skill"
	"github.com/BrutalHex/victor/robot/agent/internal/spine"
	"github.com/BrutalHex/victor/robot/agent/internal/sshctl"
	"github.com/BrutalHex/victor/robot/agent/internal/statusfile"
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
		case "face-info":
			// Read-only: what this binary would drive (no panel access).
			nIn, nLoop := face.SearchingFrames()
			fmt.Printf("spi=%s think=ddl-searching:%d+%d asset=%s telem_max=%d\n", face.Device(), nIn, nLoop, face.AssetTag, rotlog.DefaultMax)
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
	paths := []string{"/data/victor/.env", "/data/openai.key", "/anki/etc/openai"}
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
		hubHost  = flag.String("hub", env("HUB_HOST", "robot.mohammadabbasi.com"), "hub hostname or IP")
		hubPort  = flag.Int("sensor-port", 7502, "UDP SENSOR port")
		grpcPort = flag.Int("grpc-port", 7443, "TCP heartbeat/gRPC port")
		inject   = flag.String("inject", "/run/victor/inject", "unix socket for CHARGE-LATCH samples")
		logPath  = flag.String("telem-log", "/data/victor/telemetry.log", "local telemetry log")
		rate     = flag.Duration("period", 20*time.Millisecond, "control/sensor period")
		ownSpine = flag.Bool("own-spine", anki.Masked(), "stop Anki and take /dev/ttyHS0")
		spineDev = flag.String("spine", spine.DefaultDevice, "spine UART")
	)
	flag.Parse()
	_ = os.MkdirAll("/data/victor", 0755)
	_ = os.MkdirAll("/run/victor", 0755)
	loadHubEnv()
	_ = hosts.Apply("/etc/hosts", os.Getenv("HUB_IP"), env("HUB_HOST", *hubHost))

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
			stopSpine := make(chan struct{})
			go body.Serve(stopSpine)
			defer func() {
				close(stopSpine)
				<-body.Stopped()
				_ = body.Close()
			}()
			body.SetSSHLED(ssh.Enabled())
			fmt.Println("spine owned, motors held at 0")
		}
	}

	hub := &telem.Hub{Host: *hubHost, SensorPort: *hubPort, AudioPort: 7501, VideoPort: 7500, GRPCPort: *grpcPort, LogPath: *logPath}
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
	ui := &uiState{mode: "idle"}
	proc := audio.NewProcessor(audio.ModeFromEnv())
	go injectLoop(*inject, m, ssh, body, ov, ui)
	go watchdog(ssh, hub, body, ui)
	go cameraLoop(hub, lnk)
	go cmdLoop(lnk, ui, proc)
	face.Boot()
	var lastFaceAt time.Time
	// Status files: rewritten on change at most 2/s (was ~300 writes/s).
	status := statusfile.New()
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
	pk := &audio.Packetizer{}
	var lastMicLog time.Time
	for {
		select {
		case <-sig:
			return 0
		case <-tick.C:
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
				if m.Feed(latch.Sample{T: time.Since(start), Button: fr.Button, OnCharger: fr.OnCharger(), Driving: fr.Driving(), LiftNorm: fr.LiftNorm(lo, hi)}) == latch.ToggleSSH {
					on, err := ssh.Toggle()
					fmt.Printf("CHARGE-LATCH ssh.enabled=%v err=%v\n", on, err)
					showFaceUI(on, false, ui)
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
			vin := veto.Input{Cliffs: cliffs, Thresh: cal.Thresh, Calibrated: cal.Ready(), ProxMM: s.ProxMM, Forward: kind.Forward(), OnCharger: have && fr.OnCharger(), HasHeartbeat: !lnk.LastOK().IsZero(), BattMV: s.BattMV, HasCommand: lastKind != skill.Idle && lastKind != skill.Stop && lastKind != skill.Dock}
			if vin.HasHeartbeat {
				vin.HeartbeatAge = time.Since(lnk.LastOK())
			}
			if vin.HasCommand {
				vin.CommandAge = time.Since(lastCmd)
			}
			reason := veto.Check(vin)
			allow := skill.AllowWheels(skill.ExploreEnabled(), reason == veto.Clear, vin.OnCharger)
			pwm := skill.PWM(kind, allow)
			if reason == veto.Battery || reason == veto.Fall || reason == veto.Pickup || reason == veto.Cliff {
				pwm = [4]int16{}
			}
			if pwm[0] == 0 && pwm[1] == 0 && pwm[3] == 0 && reason != veto.Battery && reason != veto.Fall {
				if time.Now().UnixMilli()/1200%2 == 0 {
					pwm[3] = skill.LookUp.Head()
				} else {
					pwm[3] = skill.LookDown.Head()
				}
			}
			if body != nil {
				body.SetDrive(pwm)
				if mic := body.DrainMic(); len(mic) > 0 {
					if time.Since(lastMicLog) >= time.Second {
						e := audio.Energies(mic)
						_ = os.WriteFile("/data/victor/mics.txt", []byte(fmt.Sprintf("%d,%d,%d,%d dir=%d mode=%s ch=%d gain=%.1f\n", e[0], e[1], e[2], e[3], proc.Direction(), proc.Mode(), proc.Channel(), proc.Gain())), 0644)
						lastMicLog = time.Now()
					}
					// Process always (keeps filters/levels continuous); send only
					// while not thinking and not playing our own reply, or the
					// speaker comes back as the next utterance.
					clean := proc.Process(mic)
					if !ui.thinking() && !ui.muted() {
						for _, pkt := range pk.Push(clean) {
							lnk.QueueMedia(hub.SendAudio(pkt))
						}
					}
				}
			}
			if inj := audio.InjectMic(); len(inj) > 0 {
				for _, pkt := range pk.Push(inj) {
					lnk.QueueMedia(hub.SendAudio(pkt))
				}
			}
			if reason != lastReason {
				lastReason = reason
			}
			if time.Since(lastFaceAt) >= 80*time.Millisecond {
				renderFace(ui, reason)
				lastFaceAt = time.Now()
			}
			status.Put("/data/victor/veto.txt", reason.String()+"\n")
			ageMs := int64(0)
			if vin.HasHeartbeat {
				ageMs = vin.HeartbeatAge.Milliseconds()
			}
			status.Put("/data/victor/hb_age_ms.txt", fmt.Sprintf("%d\n", ageMs))
			status.Put("/data/victor/skill.txt", kind.String()+"\n")
			status.Put("/data/victor/motors.txt", fmt.Sprintf("%d,%d,%d,%d\n", pwm[0], pwm[1], pwm[2], pwm[3]))
			status.Put("/data/victor/drive.txt", fmt.Sprintf("allow=%v explore=%v veto=%s charger=%v skill=%s pwm=%d,%d,%d,%d batt_raw=%d chg_raw=%d\n", allow, skill.ExploreEnabled(), reason, vin.OnCharger, kind, pwm[0], pwm[1], pwm[2], pwm[3], fr.BattVoltage, fr.ChargerVoltage))
			if _, err := os.Stat("/data/victor/hub.down"); err == nil {
				lnk.Close()
			} else {
				if !lnk.LastOK().IsZero() {
					hub.NoteOK()
				}
				lnk.Tick(uint8(reason))
			}
			lnk.QueueMedia(hub.SendSensor(s))
			if gotSpine {
				status.Put("/data/victor/spine.ok", "1\n")
			}
		}
	}
}

type overlay struct {
	mu sync.Mutex
	cl *[4]uint16
	sk *skill.Kind
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
	Button    *bool      `json:"button"`
	OnCharger *bool      `json:"on_charger"`
	Driving   *bool      `json:"driving"`
	Lift      *float64   `json:"lift"`
	Phrase    string     `json:"phrase"`
	Cliffs    *[4]uint16 `json:"cliffs"`
	Skill     string     `json:"skill"`
}

func injectLoop(path string, m *latch.Machine, ssh *sshctl.Controller, body *spine.Body, ov *overlay, ui *uiState) {
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
						showFaceUI(on, false, ui)
						if body != nil {
							body.SetSSHLED(on)
						}
					} else {
						fmt.Fprintf(conn, "fsm miss\n")
					}
					continue
				}
				sample := latch.Sample{T: time.Since(start), OnCharger: true}
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
					showFaceUI(on, false, ui)
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

func watchdog(ssh *sshctl.Controller, hub *telem.Hub, body *spine.Body, ui *uiState) {
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
		// Off time survives reboot via the flag mtime.
		offFor := sshctl.OffFor(time.Now(), offSince, ssh.OffSince())
		// Without the spine we cannot see the charger; enabling SSH never
		// moves motors, so treat "unknown" as on-charger rather than lock out.
		onCharger := true
		if body != nil {
			if fr, ok := body.Last(); ok {
				onCharger = fr.OnCharger()
			}
		}
		if sshctl.WatchdogDue(offFor, hub.HeartbeatMissing(10*time.Minute), onCharger) {
			_ = ssh.Set(true)
			showFaceUI(true, true, ui)
			if body != nil {
				body.SetSSHLED(true)
			}
		}
	}
}

func renderFace(u *uiState, reason veto.Reason) {
	if u == nil {
		return
	}
	u.mu.Lock()
	u.expireLocked(time.Now())
	mode, cap, until, t0 := u.mode, u.caption, u.until, u.thinkStart
	if !until.IsZero() && time.Now().After(until) && mode != "thinking" {
		u.mode, u.caption, mode, cap = "idle", "", "idle", ""
	}
	key := mode + "|" + cap + "|" + reason.String()
	if mode == "thinking" {
		u.mu.Unlock()
		// DDL searching animation replaces the eyes for the whole turn.
		face.Thinking(time.Since(t0))
		return
	}
	blink := 0.0
	if int(time.Now().UnixMilli()/80)%40 == 0 {
		blink = 0.85
	}
	if mode != "idle" && key == u.lastDrawn {
		u.mu.Unlock()
		return
	}
	u.lastDrawn = key
	u.mu.Unlock()
	switch mode {
	case "ssh", "display":
		return
	case "name":
		face.Name(cap)
		return
	}
	if reason == veto.Cliff || reason == veto.Pickup || reason == veto.Fall || reason == veto.Battery {
		face.Blit(face.EyesCaption(strings.ToUpper(reason.String()), face.Red))
		return
	}
	lx := 0.15 * math.Sin(float64(time.Now().UnixMilli())/900.0)
	face.Blit(face.EyesFrame(lx, 0, blink))
}

func cameraLoop(hub *telem.Hub, lnk *link.Client) {
	loadCameraConf()
	camera.Start()
	t := time.NewTicker(100 * time.Millisecond)
	defer t.Stop()
	n := 0
	lastLog := time.Now()
	for range t.C {
		t0 := time.Now()
		full, nav, err := camera.Snapshot()
		if full != nil && n < 3 {
			fmt.Printf("camera snapshot %v\n", time.Since(t0))
		}
		if time.Since(lastLog) > 15*time.Second {
			st, frames, cerr := camera.Daemon.Status()
			fmt.Printf("camera %s frames=%d sent=%d err=%q last=%v\n", st, frames, n, cerr, err)
			lastLog = time.Now()
		}
		if err != nil || full == nil {
			continue
		}
		if b, err := camera.EncodeJPEG(nav, 70); err == nil {
			lnk.QueueMedia(hub.SendVideo(b, vct1.FlagNavJPEG))
		}
		n++
		if n%2 == 0 {
			if b, err := camera.EncodeJPEG(full, 75); err == nil {
				lnk.QueueMedia(hub.SendVideo(b, vct1.FlagFaceJPEG))
				camera.Remember(b)
			}
		}
	}
}

// loadCameraConf reads /data/victor/camera.conf (key=value: swap_rb, flip, gamma).
func loadCameraConf() {
	swap, flip, gamma := false, false, 0.8
	if b, err := os.ReadFile("/data/victor/camera.conf"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			k, v, ok := strings.Cut(strings.TrimSpace(line), "=")
			if !ok {
				continue
			}
			v = strings.TrimSpace(v)
			switch strings.TrimSpace(k) {
			case "swap_rb":
				swap = v == "1" || v == "true"
			case "flip":
				flip = v == "1" || v == "true"
			case "gamma":
				if g, err := strconv.ParseFloat(v, 64); err == nil {
					gamma = g
				}
			}
		}
	}
	camera.SetTone(swap, flip, gamma)
	fmt.Printf("camera conf swap_rb=%v flip=%v gamma=%.2f\n", swap, flip, gamma)
}

func cmdLoop(lnk *link.Client, ui *uiState, proc *audio.Processor) {
	for cmd := range lnk.Commands() {
		switch cmd.Kind {
		case link.CmdSpeak:
			proc.NotePlayback(cmd.Payload)
			playFor := time.Duration(len(cmd.Payload)/2) * time.Second / audio.Rate
			ui.muteFor(playFor + 500*time.Millisecond)
			// The reply is ready: thinking ends the moment the speaker starts.
			ui.speakStart()
			fmt.Printf("play start %s %dms\n", stamp(time.Now()), playFor.Milliseconds())
			_ = audio.Play(cmd.Payload)
			fmt.Printf("play end %s\n", stamp(time.Now()))
			ui.muteFor(400 * time.Millisecond)
		case link.CmdDisplay:
			if len(cmd.Payload) == face.Bytes {
				_ = os.WriteFile("/data/victor/face.rgb565", cmd.Payload, 0644)
				ui.set("display", "", 2*time.Second)
			}
		case link.CmdFaceUI:
			mode, cap, _ := strings.Cut(string(cmd.Payload), "|")
			mode = strings.ToLower(strings.TrimSpace(mode))
			switch mode {
			case "thinking":
				ui.set("thinking", cap, 0)
			case "name":
				ui.set("name", cap, 3*time.Second)
			case "idle", "e-ok", "":
				ui.set("idle", "", 0)
			default:
				ui.set("name", cap, 2*time.Second)
			}
		}
	}
}

func showFace(sshOn, auto bool) { showFaceUI(sshOn, auto, nil) }

func showFaceUI(sshOn, auto bool, ui *uiState) {
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
	ui.set("ssh", text, 1500*time.Millisecond)
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
