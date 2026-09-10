// Package sshctl starts and stops the robot SSH listener without killing
// victor-agent. WireOS uses systemd socket-activated OpenSSH (sshd.socket).
// Stock images may use dropbear.
package sshctl

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
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
	if err := os.MkdirAll("/data/victor", 0755); err != nil {
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
