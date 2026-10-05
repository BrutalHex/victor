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
	ensurePanel()
	setBacklight(10)
	// /dev/fb0 accepts the write and is not the panel the user sees.
	fbErr := writeFB(frame)
	spiErr := writeSPI(frame)
	if fbErr != nil && spiErr != nil {
		fmt.Fprintf(os.Stderr, "face blit fb=%v spi=%v\n", fbErr, spiErr)
	}
}

// Boot re-inits the panel and restores the last frame across agent restarts.
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

func writeFB(frame []byte) error {
	f, err := os.OpenFile(fbDev, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	stride := fbLineLength(f)
	if stride < Width*2 {
		stride = Width * 2
	}
	if stride == Width*2 && Height*stride == len(frame) {
		_, err = f.Write(frame)
		return err
	}
	// /dev/fb0 on Vector is often wider than 160. Packed rows shear into a blob.
	row := make([]byte, stride)
	for y := 0; y < Height; y++ {
		src := frame[y*Width*2 : (y+1)*Width*2]
		copy(row, src)
		for i := len(src); i < len(row); i++ {
			row[i] = 0
		}
		if _, err = f.WriteAt(row, int64(y*stride)); err != nil {
			return err
		}
	}
	return nil
}

func fbLineLength(f *os.File) int {
	buf := make([]byte, 256)
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), 0x4602, uintptr(unsafe.Pointer(&buf[0])))
	if errno != 0 {
		return 0
	}
	return int(binary.LittleEndian.Uint32(buf[48:52]))
}

func writeSPI(frame []byte) error {
	bumpSPIBuf()
	if err := lcdWindow(); err != nil {
		return err
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	f, err := os.OpenFile(spiDev, os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	for off := 0; off < len(frame); {
		n := spiChunk
		if n > len(frame)-off {
			n = len(frame) - off
		}
		if _, err := f.Write(frame[off : off+n]); err != nil {
			return err
		}
		off += n
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
