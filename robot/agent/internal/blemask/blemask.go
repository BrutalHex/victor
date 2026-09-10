// Package blemask disables BLE on the running image after first flash.
package blemask

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
)

const DisabledFlag = "/data/victor/ble.disabled"

var units = []string{
	"ankibluetoothd.service",
	"btproperty.service",
	"vic-switchboard.service",
}

func Disabled() bool {
	_, err := os.Stat(DisabledFlag)
	return err == nil
}

func Apply() error {
	if err := os.MkdirAll("/data/victor", 0755); err != nil {
		return err
	}
	if err := os.WriteFile(DisabledFlag, []byte("1\n"), 0644); err != nil {
		return err
	}
	_ = remountRW()
	defer remountRO()
	for _, u := range units {
		_ = exec.Command("systemctl", "stop", u).Run()
		_ = exec.Command("systemctl", "mask", u).Run()
	}
	_ = exec.Command("rfkill", "block", "bluetooth").Run()
	return nil
}

func Advertises() (bool, string) {
	out, err := exec.Command("systemctl", "is-active", "ankibluetoothd.service").CombinedOutput()
	state := strings.TrimSpace(string(out))
	if err != nil {
		return false, state
	}
	return state == "active", state
}

func remountRW() error {
	return exec.Command("mount", "-o", "remount,rw", "/").Run()
}

func remountRO() {
	_ = exec.Command("mount", "-o", "remount,ro", "/").Run()
}

func StatusLine() string {
	adv, st := Advertises()
	return fmt.Sprintf("ble.disabled=%v ankibluetoothd=%s advertising=%v", Disabled(), st, adv)
}
