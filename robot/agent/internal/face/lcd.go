package face

import (
	"encoding/binary"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

const (
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

var (
	panelReady bool
	lastWake   time.Time
)

// The Midas panel is mounted at row 24 of the controller RAM (YSHIFT 0x18 in
// lcd.c); the column window starts at 0.
const (
	rowShift = 0x18
	colShift = 0
)

// GPIOs from lcd.c: D/C, Midas reset (open drain), Santek reset.
const (
	gpioResetMidas  = 96
	gpioResetSantek = 55
)

type initStep struct {
	cmd   byte
	data  []byte
	delay time.Duration
}

// midasInit is init_scr_midas + display_on_scr_midas from lcd.c.
func midasInit() []initStep {
	return []initStep{
		{0x01, nil, 150 * time.Millisecond}, // SWRESET
		{0x11, nil, 500 * time.Millisecond}, // SLPOUT
		{0x20, nil, 0},                      // INVOFF
		{0x36, []byte{0xA8}, 0},             // MADCTL: MV|MY|BGR
		{0x3A, []byte{0x05}, 0},             // COLMOD 16 bit
		{0xE0, []byte{0x07, 0x0e, 0x08, 0x07, 0x10, 0x07, 0x02, 0x07, 0x09, 0x0f, 0x25, 0x36, 0x00, 0x08, 0x04, 0x10}, 0},
		{0xE1, []byte{0x0a, 0x0d, 0x08, 0x07, 0x0f, 0x07, 0x02, 0x07, 0x09, 0x0f, 0x25, 0x35, 0x00, 0x09, 0x04, 0x10}, 0},
		{0xFC, []byte{128 + 64}, 0},
		{0x13, nil, 100 * time.Millisecond}, // NORON
		{0x26, []byte{0x02}, 10 * time.Millisecond},
		{0x29, nil, 10 * time.Millisecond}, // DISPON
		{cmdCASET, window(colShift, Width), 0},
		{cmdRASET, window(rowShift, Height), 0},
	}
}

func window(start, n int) []byte {
	return append(u16be(start), u16be(start+n-1)...)
}

// FullInit resets the panel and runs the Midas init script, then clears RAM.
func FullInit() error {
	setBacklight(10)
	bumpSPIBuf()
	f, err := os.OpenFile(spiDev, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := spiSetup(f); err != nil {
		return err
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	// Reset pulse: Midas reset is open drain (low = drive 0, high = release).
	_ = gpioOut(gpioResetSantek, 1)
	_ = gpioOpenDrain(gpioResetMidas, false)
	_ = gpioOut(gpioResetSantek, 0)
	time.Sleep(time.Millisecond)
	_ = gpioOpenDrain(gpioResetMidas, true)
	_ = gpioOut(gpioResetSantek, 1)
	time.Sleep(120 * time.Millisecond)
	for _, st := range midasInit() {
		if err := spiCmdFD(f, st.cmd, st.data...); err != nil {
			return fmt.Errorf("cmd 0x%02x: %w", st.cmd, err)
		}
		if st.delay > 0 {
			time.Sleep(st.delay)
		}
	}
	if err := spiCmdFD(f, cmdRAMWR); err != nil {
		return err
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	if err := spiWrite(f, make([]byte, Bytes)); err != nil {
		return err
	}
	panelReady = true
	lastWake = time.Now()
	fmt.Fprintf(os.Stderr, "face panel reset + midas init on %s (hw 0x%x)\n", spiDev, hwVersion())
	return nil
}

func gpioOpenDrain(pin int, high bool) error {
	base := "/sys/class/gpio/gpio" + strconv.Itoa(pin)
	if _, err := os.Stat(base); err != nil {
		_ = os.WriteFile("/sys/class/gpio/export", []byte(strconv.Itoa(pin)+"\n"), 0644)
		time.Sleep(50 * time.Millisecond)
	}
	if high {
		return os.WriteFile(base+"/direction", []byte("in\n"), 0644)
	}
	return os.WriteFile(base+"/direction", []byte("low\n"), 0644)
}

// hwVersion is the EMR word lcd.c uses to pick Midas (>= 0x20) or Santek.
func hwVersion() uint32 {
	f, err := os.Open("/dev/mmcblk0p29")
	if err != nil {
		return 0
	}
	defer f.Close()
	var b [8]byte
	if _, err := f.Read(b[:]); err != nil {
		return 0
	}
	return binary.LittleEndian.Uint32(b[4:])
}

// waitAnimGone stops vic-anim (it sends SLPIN to the panel on exit) and waits
// for it to finish before the agent initialises the panel.
func waitAnimGone(max time.Duration) {
	_ = exec.Command("systemctl", "stop", "--no-block", "vic-anim.service", "vic-bootAnim.service").Run()
	deadline := time.Now().Add(max)
	for time.Now().Before(deadline) {
		out, _ := exec.Command("systemctl", "is-active", "vic-anim.service", "vic-bootAnim.service").Output()
		if !strings.Contains(string(out), "activ") || strings.Count(string(out), "inactive")+strings.Count(string(out), "failed") >= 2 {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
}

// spiDev is the face panel. Stock vic-anim, vic-bootAnim, vic-faultCodeDisplay
// and WireOS lcd.c all use /dev/spidev1.0; /dev/spidev0.0 is the IMU
// (vic-robot, spi_imu.h). VICTOR_FACE_SPI overrides it for experiments.
var spiDev = func() string {
	if v := os.Getenv("VICTOR_FACE_SPI"); v != "" {
		return v
	}
	return "/dev/spidev1.0"
}()

// PowerMode reads RDDPM (0x0A). Bit 4 = sleep out, bit 2 = display on, bit 7
// = booster on. Only meaningful if the panel's SDA is readable; zero or 0xff
// means nothing came back.
func PowerMode() (full byte, threeWire byte, err error) {
	f, err := os.OpenFile(spiDev, os.O_RDWR, 0)
	if err != nil {
		return 0, 0, err
	}
	defer f.Close()
	if err := spiSetup(f); err != nil {
		return 0, 0, err
	}
	if err := gpioOut(gpioDC, 0); err != nil {
		return 0, 0, err
	}
	rx := make([]byte, 2)
	if err := spiXfer(f, []byte{0x0A, 0x00}, rx); err == nil {
		full = rx[1]
	}
	// No 3-wire probe: msm spidev rejects SPI_3WIRE ("unsupported mode bits 10").
	_ = gpioOut(gpioDC, 1)
	return full, threeWire, nil
}

func spiXfer(f *os.File, tx, rx []byte) error {
	n := len(tx)
	if n == 0 {
		n = len(rx)
	}
	if n == 0 {
		return nil
	}
	x := spiIOCTransfer{length: uint32(n), speedHz: 1000000, bitsPerWord: 8}
	if len(tx) > 0 {
		x.tx = uint64(uintptr(unsafe.Pointer(&tx[0])))
	}
	if len(rx) > 0 {
		x.rx = uint64(uintptr(unsafe.Pointer(&rx[0])))
	}
	return ioctl(f, 0x40206b00, uintptr(unsafe.Pointer(&x)))
}

// writeStatus records what the agent knows about the panel for verify scripts.
func writeStatus(why string) {
	full, three, err := PowerMode()
	bl, _ := os.ReadFile("/sys/class/leds/face-backlight-left/brightness")
	line := fmt.Sprintf("%s ready=%v rddpm=0x%02x rddpm3w=0x%02x err=%v backlight=%s frames=%d at=%s\n",
		why, panelReady, full, three, err, strings.TrimSpace(string(bl)), framesSent, time.Now().Format(time.RFC3339))
	_ = os.WriteFile("/data/victor/face.txt", []byte(line), 0644)
}

func Show(text string, fg uint16) {
	Blit(EyesCaption(text, fg))
}

// Init wakes the face panel without a reset. Subcommands (ssh-on, latch
// simulate) use it from a second process while the daemon owns the panel.
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
	// The copy on /data is for debugging; once a second, not every frame
	// (25 KB × 6-12 fps of eMMC writes).
	if time.Since(lastCopy) >= time.Second {
		_ = os.MkdirAll("/data/victor", 0755)
		_ = os.WriteFile("/data/victor/face.rgb565", frame, 0644)
		lastCopy = time.Now()
	}
	if time.Since(lastBacklight) >= 5*time.Second {
		setBacklight(10)
		lastBacklight = time.Now()
	}
	if err := writeSPI(frame); err != nil {
		fmt.Fprintf(os.Stderr, "face spi: %v\n", err)
		return
	}
	if !faceLogged {
		fmt.Fprintf(os.Stderr, "face spi %s bytes %d\n", spiDev, len(frame))
		faceLogged = true
	}
}

var (
	faceLogged    bool
	framesSent    uint64
	lastCopy      time.Time
	lastBacklight time.Time
)

// Boot is the daemon's panel bring-up. vic-anim owns the panel on a stock
// boot and puts it to sleep (SLPIN) when it exits; the agent stops Anki a
// second after its own first frame, so a wake-only init lost that race and
// the face stayed black. Wait for vic-anim to be gone, then do the full
// Midas reset + init script from wire-os-victor robot/core/src/lcd.c.
func Boot() {
	waitAnimGone(8 * time.Second)
	if err := FullInit(); err != nil {
		fmt.Fprintf(os.Stderr, "face init: %v (falling back to wake)\n", err)
		Init()
	}
	EOK()
	writeStatus("boot")
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
	// Re-assert sleep-out/display-on every few seconds: anything else that
	// touches the panel (a stray vic-anim exit) must not leave it dark.
	if !panelReady || time.Since(lastWake) > 5*time.Second {
		if err := panelWake(f); err != nil {
			return err
		}
		panelReady = true
		lastWake = time.Now()
	}
	if err := spiCmdFD(f, cmdCASET, window(colShift, Width)...); err != nil {
		return err
	}
	if err := spiCmdFD(f, cmdRASET, window(rowShift, Height)...); err != nil {
		return err
	}
	if err := spiCmdFD(f, cmdRAMWR); err != nil {
		return err
	}
	if err := gpioOut(gpioDC, 1); err != nil {
		return err
	}
	// digital-dream-labs/vector faceDisplayImpl.h builds the frame with
	// cv::COLOR_RGB2BGR565. Endian stays as put() stored it. Only R and B swap.
	wire := bgr565(frame)
	if err := spiWrite(f, wire); err != nil {
		return err
	}
	framesSent++
	if framesSent%750 == 0 { // ~1 min at the 80 ms face tick
		writeStatus("run")
	}
	return nil
}

func bgr565(frame []byte) []byte {
	out := make([]byte, len(frame))
	for i := 0; i+1 < len(frame); i += 2 {
		c := binary.BigEndian.Uint16(frame[i:])
		r := (c >> 11) & 0x1f
		g := (c >> 5) & 0x3f
		b := c & 0x1f
		binary.BigEndian.PutUint16(out[i:], (b<<11)|(g<<5)|r)
	}
	return out
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
			if panelReady {
				time.Sleep(5 * time.Millisecond)
			} else {
				time.Sleep(120 * time.Millisecond)
			}
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

// gpioReady caches pins already exported as outputs so a frame costs one
// sysfs write per D/C toggle instead of three.
var (
	gpioMu    sync.Mutex
	gpioReady = map[int]bool{}
)

func gpioIsReady(pin int) bool {
	gpioMu.Lock()
	defer gpioMu.Unlock()
	return gpioReady[pin]
}

func gpioSetReady(pin int, ok bool) {
	gpioMu.Lock()
	defer gpioMu.Unlock()
	gpioReady[pin] = ok
}

func gpioOut(pin, value int) error {
	base := "/sys/class/gpio/gpio" + strconv.Itoa(pin)
	if !gpioIsReady(pin) {
		if _, err := os.Stat(base); err != nil {
			_ = os.WriteFile("/sys/class/gpio/export", []byte(strconv.Itoa(pin)+"\n"), 0644)
			time.Sleep(50 * time.Millisecond)
		}
		if err := os.WriteFile(base+"/direction", []byte("out\n"), 0644); err != nil {
			return err
		}
		gpioSetReady(pin, true)
	}
	v := "0\n"
	if value != 0 {
		v = "1\n"
	}
	if err := os.WriteFile(base+"/value", []byte(v), 0644); err != nil {
		gpioSetReady(pin, false)
		return err
	}
	return nil
}

func EOK() {
	Blit(EyesFrame(0, 0, 0))
}

func Thinking(elapsed time.Duration) {
	Blit(ThinkingFrame(elapsed))
}

// FramesSent counts frames written to the panel (for fps checks).
func FramesSent() uint64 { return framesSent }

func Name(name string) {
	Blit(EyesCaption(Caption(name), Green))
}

func Present() bool {
	_, err := os.Stat("/data/victor/face.rgb565")
	return err == nil
}
