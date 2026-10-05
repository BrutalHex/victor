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
	spiDev    = "/dev/spidev0.0"
	fbDev     = "/dev/fb0"
	gpioDC    = 110
	cmdCASET  = 0x2A
	cmdRASET  = 0x2B
	cmdRAMWR  = 0x2C
	cmdSLPOUT = 0x11
	cmdDISPON = 0x29
	cmdCOLMOD = 0x3A
	cmdMADCTL = 0x36
	spiChunk  = 4096
)

var panelReady bool

func Show(text string, fg uint16) {
	Blit(EyesCaption(text, fg))
}

// Init wakes the face panel. A robot reset leaves the controller asleep, so a
// later pixel write can succeed and still show nothing.
func Init() {
	setBacklight(10)
	if panelReady {
		return
	}
	f, err := os.OpenFile(spiDev, os.O_RDWR, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "face spi: %v\n", err)
		return
	}
	defer f.Close()
	if err := spiSetup(f); err != nil {
		fmt.Fprintf(os.Stderr, "face spi: %v\n", err)
		return
	}
	if err := panelWake(f); err != nil {
		fmt.Fprintf(os.Stderr, "face spi: %v\n", err)
		return
	}
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
		return
	}
	if !faceLogged {
		fmt.Fprintf(os.Stderr, "face spi %s bytes %d\n", spiDev, len(frame))
		faceLogged = true
	}
}

var faceLogged bool

func Boot() {
	Init()
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

// spiIOCTransfer matches the 3.18 spidev struct (no word_delay field).
type spiIOCTransfer struct {
	tx          uint64
	rx          uint64
	length      uint32
	speedHz     uint32
	delayUsecs  uint16
	bitsPerWord uint8
	csChange    uint8
	pad         uint32
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
		if err := panelWake(f); err != nil {
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
	wire := make([]byte, len(frame))
	for i := 0; i+1 < len(frame); i += 2 {
		wire[i] = frame[i+1]
		wire[i+1] = frame[i]
	}
	return spiWrite(f, wire)
}

func spiSetup(f *os.File) error {
	mode := uint8(0)
	bits := uint8(8)
	speed := uint32(8000000)
	if err := ioctl(f, 0x40016b01, uintptr(unsafe.Pointer(&mode))); err != nil {
		return err
	}
	if err := ioctl(f, 0x40016b03, uintptr(unsafe.Pointer(&bits))); err != nil {
		return err
	}
	return ioctl(f, 0x40046b04, uintptr(unsafe.Pointer(&speed)))
}

// panelWakeCmds leaves geometry alone. This kernel has CONFIG_FB disabled, so
// there is no /dev/fb0, but the bootloader already programmed the controller.
// SWRESET (0x01), COLMOD, or MADCTL shears the 160×80 image.
func panelWakeCmds() []byte {
	return []byte{cmdSLPOUT, cmdDISPON}
}

func panelWake(f *os.File) error {
	for _, cmd := range panelWakeCmds() {
		if err := spiCmdFD(f, cmd); err != nil {
			return err
		}
		if cmd == cmdSLPOUT {
			time.Sleep(120 * time.Millisecond)
		}
	}
	time.Sleep(20 * time.Millisecond)
	return nil
}

func spiCmdFD(f *os.File, cmd byte, data ...byte) error {
	if err := gpioOut(gpioDC, 0); err != nil {
		return err
	}
	if err := spiWrite(f, []byte{cmd}); err != nil {
		return err
	}
	if len(data) == 0 {
		return nil
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	return spiWrite(f, data)
}

func spiWrite(f *os.File, buf []byte) error {
	// SPI_IOC_MESSAGE(1) on a 32-byte transfer. write() succeeds without applying mode.
	xfer := spiIOCTransfer{
		tx:          uint64(uintptr(unsafe.Pointer(&buf[0]))),
		length:      uint32(len(buf)),
		speedHz:     8000000,
		bitsPerWord: 8,
	}
	return ioctl(f, 0x40206b00, uintptr(unsafe.Pointer(&xfer)))
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
