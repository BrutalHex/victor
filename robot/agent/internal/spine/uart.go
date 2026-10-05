package spine

import (
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
)

const DefaultDevice = "/dev/ttyHS0"

const (
	// pumpEvery matches the body mic block: 80 samples at 16 kHz is 5 ms.
	// The control loop must not run slower than this. A 20 ms tick that also
	// blits the face lets the 3 Mbaud stream fill the msm_serial_hs buffer;
	// the driver then logs "tty buffer exhausted" and stops delivering bytes.
	pumpEvery     = 5 * time.Millisecond
	rxStaleAfter  = 400 * time.Millisecond
	rxRecoverGap  = time.Second
	writeDeadline = 30 * time.Millisecond
)

// Body is the syscon UART. Serve owns the file descriptor.
type Body struct {
	mu          sync.Mutex
	f           *os.File
	dev         string
	buf         []byte
	seq         uint32
	last        Frame
	ok          bool
	liftMin     int32
	liftMax     int32
	leds        [12]byte
	drive       [4]int16
	mic         []int16
	shortLogs   int
	lastFrame   time.Time
	lastMode    time.Time
	lastRecover time.Time
	done        chan struct{}
}

func Open(dev string) (*Body, error) {
	if dev == "" {
		dev = DefaultDevice
	}
	f, err := openPort(dev)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	b := &Body{
		f:         f,
		dev:       dev,
		buf:       make([]byte, 0, 8192),
		liftMin:   0,
		liftMax:   1,
		lastFrame: now,
		lastMode:  now,
		done:      make(chan struct{}),
	}
	if err := b.send(EncodeMode(ModeRun)); err != nil {
		_ = f.Close()
		return nil, err
	}
	_ = b.send(Encode(TypeVersion, nil))
	return b, nil
}

// Serve reads and writes the spine UART until stop is closed.
// It has to stay off the face/status loop: that loop blocks long enough
// for msm_serial_hs to stall RX, after which DrainMic stays empty and the
// hub gets no audio.
func (b *Body) Serve(stop <-chan struct{}) {
	if b == nil || b.done == nil {
		return
	}
	defer close(b.done)
	var lastCtrl time.Time
	var lastLog time.Time
	logErr := func(err error) {
		if err == nil || time.Since(lastLog) < time.Second {
			return
		}
		fmt.Fprintf(os.Stderr, "spine: %v\n", err)
		lastLog = time.Now()
	}
	for {
		if stopped(stop) {
			return
		}
		now := time.Now()
		if lastCtrl.IsZero() || now.Sub(lastCtrl) >= pumpEvery {
			logErr(b.writeCycle())
			lastCtrl = now
		}
		n, err := b.readAll()
		logErr(err)
		if b.needsRecover() {
			logErr(b.reopen())
			lastCtrl = time.Now()
			continue
		}
		if n > 0 {
			continue
		}
		// Idle: block until the next bytes or the next control slot.
		// A stalled msm_serial_hs fd can wake Poll and then read EAGAIN;
		// sleep in that case so the loop cannot peg a core.
		if b.waitRX(pumpEvery) > 0 {
			n, err = b.readAll()
			logErr(err)
			if n == 0 {
				time.Sleep(pumpEvery)
			}
		}
	}
}

// Stopped is closed when Serve returns.
func (b *Body) Stopped() <-chan struct{} {
	if b == nil || b.done == nil {
		ch := make(chan struct{})
		close(ch)
		return ch
	}
	return b.done
}

func stopped(stop <-chan struct{}) bool {
	select {
	case <-stop:
		return true
	default:
		return false
	}
}

func (b *Body) Close() error {
	if b == nil {
		return nil
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.f == nil {
		return nil
	}
	err := b.f.Close()
	b.f = nil
	return err
}

func (b *Body) Last() (Frame, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.last, b.ok
}

func (b *Body) SetDrive(m [4]int16) {
	b.mu.Lock()
	b.drive = m
	b.mu.Unlock()
}

func (b *Body) Drive() [4]int16 {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.drive
}

func (b *Body) SetSSHLED(on bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if on {
		// back, middle, front: green-ish BGR bytes used by the body
		b.leds = [12]byte{0x00, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0x00, 0xFF, 0x00}
	} else {
		b.leds = [12]byte{}
	}
}

func (b *Body) writeCycle() error {
	b.mu.Lock()
	leds := b.leds
	seq := b.seq
	b.seq++
	sendMode := b.lastMode.IsZero() || time.Since(b.lastMode) >= time.Second
	b.mu.Unlock()

	if sendMode {
		// The body drops the 640-byte mic block if run mode is not refreshed.
		if err := b.send(EncodeMode(ModeRun)); err != nil {
			return err
		}
		b.mu.Lock()
		b.lastMode = time.Now()
		b.mu.Unlock()
	}
	return b.send(EncodeCtrl(seq, b.Drive(), leds))
}

func (b *Body) readAll() (int, error) {
	b.mu.Lock()
	f := b.f
	b.mu.Unlock()
	if f == nil {
		return 0, errors.New("spine closed")
	}
	tmp := make([]byte, 8192)
	total := 0
	err := drainReads(f.Read, tmp, func(p []byte) {
		total += len(p)
		b.appendRX(p)
	})
	return total, err
}

// drainReads keeps reading until the port is empty. One read per tick
// leaves the rest of the body stream in the kernel buffer until it stalls.
func drainReads(read func([]byte) (int, error), tmp []byte, ingest func([]byte)) error {
	if len(tmp) == 0 {
		return errors.New("empty read buffer")
	}
	for i := 0; i < 64; i++ {
		n, err := read(tmp)
		if n > 0 {
			ingest(tmp[:n])
		}
		if err != nil {
			if isAgain(err) || errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		if n < len(tmp) {
			return nil
		}
	}
	return nil
}

func (b *Body) appendRX(p []byte) {
	if len(p) == 0 {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.buf = append(b.buf, p...)
	if len(b.buf) > 1<<16 {
		b.buf = b.buf[len(b.buf)/2:]
	}
	rest, frames := Split(b.buf)
	b.buf = append(b.buf[:0], rest...)
	for _, fr := range frames {
		if fr.Type != TypeData && fr.Type != 0x6B61 {
			continue
		}
		f, err := ParseData(fr.Payload)
		if err != nil {
			continue
		}
		b.last = f
		b.ok = true
		b.lastFrame = time.Now()
		if len(f.Mic) > 0 {
			b.mic = append(b.mic, f.Mic...)
			if len(b.mic) > 16000 {
				b.mic = b.mic[len(b.mic)-8000:]
			}
			b.shortLogs = 0
		} else if b.shortLogs < 4 {
			b.shortLogs++
			fmt.Fprintf(os.Stderr, "spine data %d bytes, no mic block\n", len(fr.Payload))
		}
		if b.liftMin == 0 && b.liftMax == 1 {
			b.liftMin = f.Motors[2].Pos
			b.liftMax = f.Motors[2].Pos + 1
		}
		if f.Motors[2].Pos < b.liftMin {
			b.liftMin = f.Motors[2].Pos
		}
		if f.Motors[2].Pos > b.liftMax {
			b.liftMax = f.Motors[2].Pos
		}
	}
}

func (b *Body) needsRecover() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return rxNeedsRecover(b.lastFrame, b.lastRecover, time.Now(), rxStaleAfter, rxRecoverGap)
}

func rxNeedsRecover(lastFrame, lastTry, now time.Time, stale, gap time.Duration) bool {
	if now.Sub(lastFrame) < stale {
		return false
	}
	if !lastTry.IsZero() && now.Sub(lastTry) < gap {
		return false
	}
	return true
}

// reopen clears an msm_serial_hs RX stall. After "tty buffer exhausted"
// the driver stops DMA and a later read only returns EAGAIN.
func (b *Body) reopen() error {
	f, err := openPort(b.dev)
	if err != nil {
		b.mu.Lock()
		b.lastRecover = time.Now()
		b.mu.Unlock()
		return err
	}
	b.mu.Lock()
	old := b.f
	b.f = f
	b.buf = nil
	now := time.Now()
	b.lastFrame = now
	b.lastRecover = now
	b.mu.Unlock()
	if old != nil && old != f {
		_ = old.Close()
	}
	fmt.Fprintf(os.Stderr, "spine rx stalled, reopening %s\n", b.dev)
	if err := b.send(EncodeMode(ModeRun)); err != nil {
		return err
	}
	b.mu.Lock()
	b.lastMode = time.Now()
	b.mu.Unlock()
	_ = b.send(Encode(TypeVersion, nil))
	return nil
}

// waitRX blocks until the UART is readable or timeout elapses.
// It returns the number of poll events (0 on timeout).
func (b *Body) waitRX(timeout time.Duration) int {
	b.mu.Lock()
	f := b.f
	b.mu.Unlock()
	if f == nil {
		time.Sleep(timeout)
		return 0
	}
	ms := int(timeout / time.Millisecond)
	if ms < 1 {
		ms = 1
	}
	pfd := []unix.PollFd{{Fd: int32(f.Fd()), Events: unix.POLLIN}}
	n, _ := unix.Poll(pfd, ms)
	return n
}

func (b *Body) LiftRange() (int32, int32) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.liftMin, b.liftMax
}

// DrainMic returns interleaved 4-channel spine PCM since the last drain.
func (b *Body) DrainMic() []int16 {
	if b == nil {
		return nil
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.mic) == 0 {
		return nil
	}
	out := b.mic
	b.mic = nil
	return out
}

func (b *Body) send(p []byte) error {
	b.mu.Lock()
	f := b.f
	b.mu.Unlock()
	if f == nil {
		return errors.New("spine closed")
	}
	return writeFull(f, p)
}

func writeFull(w io.Writer, p []byte) error {
	deadline := time.Now().Add(writeDeadline)
	for len(p) > 0 {
		n, err := w.Write(p)
		if n > 0 {
			p = p[n:]
			if len(p) == 0 {
				return nil
			}
			if err == nil || isAgain(err) {
				continue
			}
			return err
		}
		if err == nil {
			return io.ErrShortWrite
		}
		if isAgain(err) && time.Now().Before(deadline) {
			time.Sleep(time.Millisecond)
			continue
		}
		return err
	}
	return nil
}

func isAgain(err error) bool {
	if err == nil {
		return false
	}
	if os.IsTimeout(err) {
		return true
	}
	if errors.Is(err, syscall.EAGAIN) || errors.Is(err, syscall.EWOULDBLOCK) {
		return true
	}
	var pe *os.PathError
	if errors.As(err, &pe) {
		return pe.Err == syscall.EAGAIN || pe.Err == syscall.EWOULDBLOCK
	}
	return false
}

func openPort(dev string) (*os.File, error) {
	f, err := os.OpenFile(dev, os.O_RDWR, 0)
	if err != nil {
		return nil, err
	}
	if err := setRaw3M(int(f.Fd())); err != nil {
		_ = f.Close()
		return nil, err
	}
	flushBoth(int(f.Fd()))
	return f, nil
}

func flushBoth(fd int) {
	// TCIOFLUSH is the TCFLSH argument, not an ioctl number. x/sys does not export it.
	const tcIOFlush = 2
	_, _, _ = unix.Syscall(unix.SYS_IOCTL, uintptr(fd), uintptr(unix.TCFLSH), tcIOFlush)
}

func setRaw3M(fd int) error {
	var t unix.Termios
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(fd), uintptr(unix.TCGETS), uintptr(unsafe.Pointer(&t)))
	if errno != 0 {
		return fmt.Errorf("tcgets: %w", errno)
	}
	t.Iflag &^= unix.IGNBRK | unix.BRKINT | unix.PARMRK | unix.ISTRIP | unix.INLCR | unix.IGNCR | unix.ICRNL | unix.IXON
	t.Oflag &^= unix.OPOST
	t.Lflag &^= unix.ECHO | unix.ECHONL | unix.ICANON | unix.ISIG | unix.IEXTEN
	t.Cflag &^= unix.CSIZE | unix.PARENB
	t.Cflag |= unix.CS8 | unix.CREAD | unix.CLOCAL | unix.B3000000
	t.Ispeed = unix.B3000000
	t.Ospeed = unix.B3000000
	t.Cc[unix.VMIN] = 0
	t.Cc[unix.VTIME] = 0
	_, _, errno = unix.Syscall(unix.SYS_IOCTL, uintptr(fd), uintptr(unix.TCSETS), uintptr(unsafe.Pointer(&t)))
	if errno != 0 {
		return fmt.Errorf("tcsets: %w", errno)
	}
	_ = syscall.SetNonblock(fd, true)
	return nil
}
