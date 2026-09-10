// Package anki stops stock Vector services so victor-agent can own the spine.
package anki

import (
	"os"
	"os/exec"
	"strings"
)

const Flag = "/data/victor/anki.masked"

func Mask() error {
	_ = os.MkdirAll("/data/victor", 0755)
	if err := os.WriteFile(Flag, []byte("1\n"), 0644); err != nil {
		return err
	}
	_ = exec.Command("systemctl", "stop", "anki-robot.target").Run()
	// Do not mask sshd.socket.
	return nil
}

func Restore() error {
	_ = os.Remove(Flag)
	return exec.Command("systemctl", "start", "anki-robot.target").Run()
}

func Masked() bool {
	_, err := os.Stat(Flag)
	return err == nil
}

func EngineActive() bool {
	out, err := exec.Command("systemctl", "is-active", "vic-engine.service").CombinedOutput()
	return err == nil && strings.TrimSpace(string(out)) == "active"
}
