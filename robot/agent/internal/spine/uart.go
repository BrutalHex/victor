package spine

import (
	"errors"
	"fmt"
	"os"
	"sync"
	"syscall"
	"unsafe"

	"golang.org/x/sys/unix"
)

const DefaultDevice = "/dev/ttyHS0"

type Body struct {
	mu     sync.Mutex
	f      *os.File
	buf    []byte
	seq    uint32
	last   Frame
	ok     bool
	liftMin int32
	liftMax int32
	leds   [12]byte
}

func Open(dev string) (*Body, error) {
	if dev == "" {
		dev = DefaultDevice
	}
	f, err := os.OpenFile(dev, os.O_RDWR, 0)
	if err != nil {
		return nil, err
	}
	if err := setRaw3M(int(f.Fd())); err != nil {
		_ = f.Close()
		return nil, err
	}
	b := &Body{f: f, buf: make([]byte, 0, 8192), liftMin: 0, liftMax: 1}
	_ = b.send(Encode(TypeMode, nil))
	_ = b.send(Encode(TypeVersion, nil))
	return b, nil
}

func (b *Body) Close() error {
	if b == nil || b.f == nil {
		return nil
	}
	return b.f.Close()
}

func (b *Body) Last() (Frame, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.last, b.ok
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

func (b *Body) Pump() error {
	b.mu.Lock()
	leds := b.leds
	seq := b.seq
	b.seq++
	b.mu.Unlock()

	// Always zero PWM. Do not guess motor signs on a live desk.
	if err := b.send(EncodeCtrl(seq, [4]int16{}, leds)); err != nil {
		return err
	}
	tmp := make([]byte, 4096)
	n, err := b.f.Read(tmp)
	if n > 0 {
		b.mu.Lock()
		b.buf = append(b.buf, tmp[:n]...)
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
		b.mu.Unlock()
	}
	if err != nil && !isAgain(err) {
		return err
	}
	return nil
}

func isAgain(err error) bool {
	if err == nil || os.IsTimeout(err) {
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

func (b *Body) LiftRange() (int32, int32) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.liftMin, b.liftMax
}

func (b *Body) send(p []byte) error {
	_, err := b.f.Write(p)
	return err
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
