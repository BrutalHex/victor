// Package sshctl starts and stops the robot SSH listener without killing
// victor-agent. WireOS uses systemd socket-activated OpenSSH (sshd.socket).
// Stock images may use dropbear.
package sshctl

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const FlagPath = "/data/victor/ssh.enabled"

type Controller struct {
	Flag     string
	Socket   string
	Dropbear string
}

func New() *Controller {
	return &Controller{
		Flag:     FlagPath,
		Socket:   "sshd.socket",
		Dropbear: "dropbear.service",
	}
}

func (c *Controller) Enabled() bool {
	b, err := os.ReadFile(c.Flag)
	if err != nil {
		return true // first boot of our image: SSH ON
	}
	s := strings.TrimSpace(string(b))
	return s == "1" || strings.EqualFold(s, "on") || s == ""
}

func (c *Controller) Set(on bool) error {
	if err := os.MkdirAll(filepath.Dir(c.Flag), 0755); err != nil {
		return err
	}
	val := "0"
	if on {
		val = "1"
	}
	if err := os.WriteFile(c.Flag, []byte(val+"\n"), 0644); err != nil {
		return err
	}
	return c.Apply()
}

// OffSince returns the mtime of the flag file. Set writes the flag, so when
// SSH is off this is when it was turned off; it survives reboots, which keeps
// the 24 h watchdog clock honest across power cycles.
func (c *Controller) OffSince() time.Time {
	fi, err := os.Stat(c.Flag)
	if err != nil {
		return time.Time{}
	}
	return fi.ModTime()
}

// WatchdogOffAfter is how long SSH must be off before the watchdog may
// re-enable it (GROK_INSTRUCTIONS.md: 24 h AND hub heartbeat missing 10 min
// AND on charger).
const WatchdogOffAfter = 24 * time.Hour

// WatchdogDue decides whether the SSH watchdog should auto-enable SSH.
func WatchdogDue(offFor time.Duration, heartbeatMissing, onCharger bool) bool {
	return offFor >= WatchdogOffAfter && heartbeatMissing && onCharger
}

// OffFor combines the in-process off timer with the persisted flag mtime.
// Vector has no RTC, so a flag mtime before 2020 or in the future is ignored.
func OffFor(now, inProcessSince, flagMtime time.Time) time.Duration {
	var d time.Duration
	if !inProcessSince.IsZero() {
		d = now.Sub(inProcessSince)
	}
	if !flagMtime.IsZero() && flagMtime.Year() >= 2020 && !flagMtime.After(now) {
		if fd := now.Sub(flagMtime); fd > d {
			d = fd
		}
	}
	return d
}

func (c *Controller) Toggle() (bool, error) {
	on := !c.Enabled()
	return on, c.Set(on)
}

func (c *Controller) Apply() error {
	on := c.Enabled()
	if unitExists(c.Socket) {
		if on {
			return run("systemctl", "start", c.Socket)
		}
		return run("systemctl", "stop", c.Socket)
	}
	if unitExists(c.Dropbear) {
		if on {
			return run("systemctl", "start", c.Dropbear)
		}
		return run("systemctl", "stop", c.Dropbear)
	}
	return fmt.Errorf("no sshd.socket or dropbear.service")
}

// Active reports whether the SSH listener unit is actually running
// (sshd.socket listening, or dropbear.service active).
func (c *Controller) Active() bool {
	for _, u := range []string{c.Socket, c.Dropbear} {
		if unitExists(u) {
			return exec.Command("systemctl", "is-active", "--quiet", u).Run() == nil
		}
	}
	return false
}

// LatchFlag re-arms the old CHARGE-LATCH button gesture. Default absent = off:
// since 10 Oct 2026 SSH is toggled by voice only (owner's request); the button
// field reads "pressed" on every frame on this robot, so it must not toggle.
var LatchFlag = "/data/victor/charge-latch.enabled"

func LatchEnabled() bool {
	_, err := os.Stat(LatchFlag)
	return err == nil
}

// VoiceAction maps hub action names to the requested SSH state.
// ok=false: not an SSH action. status=true: report only, change nothing.
func VoiceAction(name string) (on, status, ok bool) {
	switch name {
	case "ssh_on":
		return true, false, true
	case "ssh_off":
		return false, false, true
	case "ssh_status":
		return false, true, true
	}
	return false, false, false
}

func unitExists(name string) bool {
	err := exec.Command("systemctl", "cat", name).Run()
	return err == nil
}

func run(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %s: %w (%s)", name, strings.Join(args, " "), err, bytesPreview(out))
	}
	return nil
}

func bytesPreview(b []byte) string {
	s := strings.TrimSpace(string(b))
	if len(s) > 200 {
		return s[:200]
	}
	return s
}
