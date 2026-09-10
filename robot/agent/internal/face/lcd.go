package face

import (
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	spiDev   = "/dev/spidev1.0"
	fbDev    = "/dev/fb0"
	gpioDC   = 110
	cmdWrite = 0x2C
)

func Show(text string, fg uint16) {
	frame := Frame(text, fg)
	_ = os.WriteFile("/data/victor/face.rgb565", frame, 0644)
	setBacklight(10)
	if err := writeFB(frame); err == nil {
		return
	}
	_ = writeSPI(frame)
}

func setBacklight(level int) {
	if level < 0 {
		level = 0
	}
	if level > 20 {
		level = 20
	}
	s := strconv.Itoa(level) + "\n"
	_ = os.WriteFile("/sys/class/leds/face-backlight-left/brightness", []byte(s), 0644)
	_ = os.WriteFile("/sys/class/leds/face-backlight-right/brightness", []byte(s), 0644)
}

func writeFB(frame []byte) error {
	f, err := os.OpenFile(fbDev, os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(frame)
	return err
}

func writeSPI(frame []byte) error {
	if err := gpioOut(gpioDC, 0); err != nil {
		return err
	}
	f, err := os.OpenFile(spiDev, os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := f.Write([]byte{cmdWrite}); err != nil {
		return err
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	_, err = f.Write(frame)
	return err
}

func gpioOut(pin, value int) error {
	base := "/sys/class/gpio/gpio" + strconv.Itoa(pin)
	if _, err := os.Stat(base); err != nil {
		_ = os.WriteFile("/sys/class/gpio/export", []byte(strconv.Itoa(pin)+"\n"), 0644)
		time.Sleep(50 * time.Millisecond)
	}
	if err := os.WriteFile(base+"/direction", []byte("out\n"), 0644); err != nil {
		return err
	}
	v := "0\n"
	if value != 0 {
		v = "1\n"
	}
	return os.WriteFile(base+"/value", []byte(v), 0644)
}

func EOK() {
	Show("E-OK", Green)
}

func Present() bool {
	_, err := os.Stat("/data/victor/face.rgb565")
	return err == nil
}

func Caption(s string) string {
	return strings.ToUpper(strings.TrimSpace(s))
}
