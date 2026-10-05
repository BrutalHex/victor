package face

import (
	"encoding/binary"
	"fmt"
	"os"
	"strconv"
	"syscall"
	"time"
	"unsafe"
)

const (
	spiDev   = "/dev/spidev0.0"
	fbDev    = "/dev/fb0"
	gpioDC   = 110
	cmdCASET = 0x2A
	cmdRASET = 0x2B
	cmdRAMWR = 0x2C
	cmdSLPOUT = 0x11
	cmdDISPON = 0x29
	cmdCOLMOD = 0x3A
	cmdMADCTL = 0x36
	spiChunk = 4096
)

var panelReady bool

func Show(text string, fg uint16) {
	Blit(EyesCaption(text, fg))
}

// Init wakes the face panel. A robot reset leaves the controller asleep, so a
// later fb write can succeed and still show nothing.
func Init() {
	// Do not SWRESET or rewrite MADCTL. That scrambles a panel the kernel
	// already programmed, and the eyes come out as a sheared shape.
	setBacklight(10)
	panelReady = true
}

func ensurePanel() {
	if panelReady {
		return
	}
	Init()
}

func Blit(frame []byte) {
	if len(frame) != Bytes {
		return
	}
	_ = os.MkdirAll("/data/victor", 0755)
	_ = os.WriteFile("/data/victor/face.rgb565", frame, 0644)
	setBacklight(10)
	if err := writeSPI(frame); err != nil {
		fmt.Fprintf(os.Stderr, "face spi: %v\n", err)
	}
}

// Boot draws a fresh frame. This robot has no /dev/fb0.
func Boot() {
	EOK()
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

func writeSPI(frame []byte) error {
	f, err := os.OpenFile(spiDev, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := spiSetup(f); err != nil {
		return err
	}
	if !panelReady {
		if err := panelInit(f); err != nil {
			return err
		}
		panelReady = true
	}
	if err := spiCmdFD(f, cmdCASET, append(u16be(0), u16be(Width-1)...)...); err != nil {
		return err
	}
	if err := spiCmdFD(f, cmdRASET, append(u16be(0), u16be(Height-1)...)...); err != nil {
		return err
	}
	if err := spiCmdFD(f, cmdRAMWR); err != nil {
		return err
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	// Anki screen.rgb565_bytepair is little-endian on the wire. Our buffer is big-endian.
	wire := make([]byte, len(frame))
	for i := 0; i+1 < len(frame); i += 2 {
		wire[i] = frame[i+1]
		wire[i+1] = frame[i]
	}
	for off := 0; off < len(wire); {
		n := spiChunk
		if n > len(wire)-off {
			n = len(wire) - off
		}
		if _, err := f.Write(wire[off : off+n]); err != nil {
			return err
		}
		off += n
	}
	return nil
}

func spiSetup(f *os.File) error {
	mode := uint8(0)
	bits := uint8(8)
	speed := uint32(8000000)
	if err := ioctl(f, 0x40016b01, uintptr(unsafe.Pointer(&mode))); err != nil { // SPI_IOC_WR_MODE
		return err
	}
	if err := ioctl(f, 0x40016b03, uintptr(unsafe.Pointer(&bits))); err != nil { // SPI_IOC_WR_BITS_PER_WORD
		return err
	}
	return ioctl(f, 0x40046b04, uintptr(unsafe.Pointer(&speed))) // SPI_IOC_WR_MAX_SPEED_HZ
}

func panelInit(f *os.File) error {
	if err := spiCmdFD(f, 0x01); err != nil { // SWRESET
		return err
	}
	time.Sleep(50 * time.Millisecond)
	if err := spiCmdFD(f, cmdSLPOUT); err != nil {
		return err
	}
	time.Sleep(120 * time.Millisecond)
	if err := spiCmdFD(f, cmdCOLMOD, 0x05); err != nil { // RGB565
		return err
	}
	if err := spiCmdFD(f, cmdMADCTL, 0x00); err != nil {
		return err
	}
	if err := spiCmdFD(f, cmdDISPON); err != nil {
		return err
	}
	time.Sleep(20 * time.Millisecond)
	return nil
}

func spiCmdFD(f *os.File, cmd byte, data ...byte) error {
	if err := gpioOut(gpioDC, 0); err != nil {
		return err
	}
	if _, err := f.Write([]byte{cmd}); err != nil {
		return err
	}
	if len(data) == 0 {
		return nil
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	_, err := f.Write(data)
	return err
}

func ioctl(f *os.File, req uintptr, arg uintptr) error {
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), req, arg)
	if errno != 0 {
		return errno
	}
	return nil
}

func lcdWindow() error {
	cols := append(u16be(0), u16be(Width-1)...)
	rows := append(u16be(0), u16be(Height-1)...)
	if err := spiCmd(cmdCASET, cols...); err != nil {
		return err
	}
	if err := spiCmd(cmdRASET, rows...); err != nil {
		return err
	}
	return spiCmd(cmdRAMWR)
}

func spiCmd(cmd byte, data ...byte) error {
	if err := gpioOut(gpioDC, 0); err != nil {
		return err
	}
	f, err := os.OpenFile(spiDev, os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	if _, err := f.Write([]byte{cmd}); err != nil {
		_ = f.Close()
		return err
	}
	if len(data) == 0 {
		return f.Close()
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		_ = f.Close()
		return err
	}
	_, err = f.Write(data)
	_ = f.Close()
	return err
}

func u16be(v int) []byte {
	var b [2]byte
	binary.BigEndian.PutUint16(b[:], uint16(v))
	return b[:]
}

func bumpSPIBuf() {
	_ = os.WriteFile("/sys/module/spidev/parameters/bufsiz", []byte("65536\n"), 0644)
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
	Blit(EyesFrame(0, 0, 0))
}

func Thinking(phase float64) {
	Blit(EyesThinking(phase))
}

func Name(name string) {
	Blit(EyesCaption(Caption(name), Green))
}

func Present() bool {
	_, err := os.Stat("/data/victor/face.rgb565")
	return err == nil
}
