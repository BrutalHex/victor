package camera

// Client for the stock vicos camera daemon (mm-anki-camera.service).
//
// Protocol (from Anki's camera_client.c / camera_server.c, see DDL
// digital-dream-labs/vector platform/camera/vicos and vicos-oelinux
// mm-camera-anki): a unix DGRAM socket at ServerSock carries fixed 144-byte
// messages {msg_id, version, client_id, fd, payload[128]}. REGISTER makes the
// server allocate an ION buffer and send its fd (SCM_RIGHTS) in a BUFFER
// message; START begins capture; the client must send HEARTBEAT at least every
// 500 ms or the server drops it and stops the sensor. The shared buffer holds a
// header ("CAM0", write_idx, 6 slot locks, offsets) and 6 frame slots; the
// default format is RGB888 640x360 (debayered + 2x downsampled 1280x720).

import (
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
)

var (
	ServerSock = "/var/run/mm-anki-camera/camera-server"
	ClientSock = "/run/victor-agent-cam_client0"
)

const (

	msgSize       = 144
	c2sHeartbeat  = 0
	c2sRegister   = 1
	c2sUnregister = 2
	c2sStart      = 3
	s2cStatus     = 6
	s2cBuffer     = 7
	s2cHeartbeat  = 8

	maxFrames    = 6
	frameHdrLen  = 60 // anki_camera_frame_t up to data[] (timestamp u64 + 5 u32 + 4 u8 + 8 u32 pad)
	FormatRAW    = 0
	FormatRGB888 = 1
	FormatYUV    = 2

	heartbeatEvery = 200 * time.Millisecond
)

// Frame is one copied camera frame.
type Frame struct {
	ID        uint32
	Timestamp uint64
	W, H      int
	Stride    int
	Format    uint8
	Data      []byte
}

// Anki keeps a registered, running session with mm-anki-camera.
type Anki struct {
	mu      sync.Mutex
	fd      int
	mem     []byte
	status  string
	lastID  uint32
	started bool
	Frames  uint64
	Err     string
}

var ErrNoFrame = errors.New("camera: no new frame")

func encodeMsg(id uint32, payload []byte) []byte {
	b := make([]byte, msgSize)
	binary.LittleEndian.PutUint32(b[0:], id)
	binary.LittleEndian.PutUint32(b[12:], 0xffffffff) // fd = -1
	copy(b[16:], payload)
	return b
}

// Run connects and keeps the session alive forever (reconnects on errors).
func (a *Anki) Run(stop <-chan struct{}) {
	for {
		err := a.session(stop)
		a.mu.Lock()
		a.closeLocked()
		if err != nil {
			a.Err = err.Error()
		}
		a.mu.Unlock()
		select {
		case <-stop:
			return
		case <-time.After(2 * time.Second):
		}
	}
}

func (a *Anki) Status() (string, uint64, string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.status, a.Frames, a.Err
}

func (a *Anki) closeLocked() {
	if a.mem != nil {
		_ = unix.Munmap(a.mem)
		a.mem = nil
	}
	if a.fd > 0 {
		_ = unix.Close(a.fd)
		a.fd = 0
	}
	a.status = "offline"
	a.started = false
}

func (a *Anki) send(id uint32) error {
	_, err := unix.Write(a.fd, encodeMsg(id, nil))
	return err
}

func (a *Anki) session(stop <-chan struct{}) error {
	fd, err := unix.Socket(unix.AF_UNIX, unix.SOCK_DGRAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return err
	}
	_ = os.Remove(ClientSock)
	if err := unix.Bind(fd, &unix.SockaddrUnix{Name: ClientSock}); err != nil {
		unix.Close(fd)
		return fmt.Errorf("bind: %w", err)
	}
	if err := unix.Connect(fd, &unix.SockaddrUnix{Name: ServerSock}); err != nil {
		unix.Close(fd)
		return fmt.Errorf("connect %s: %w", ServerSock, err)
	}
	a.mu.Lock()
	a.fd = fd
	a.status = "registering"
	a.mu.Unlock()
	if err := a.send(c2sRegister); err != nil {
		return err
	}
	buf := make([]byte, msgSize)
	oob := make([]byte, unix.CmsgSpace(4))
	lastHB := time.Now()
	lastRx := time.Now()
	for {
		select {
		case <-stop:
			_ = a.send(c2sUnregister)
			return nil
		default:
		}
		if time.Since(lastHB) >= heartbeatEvery {
			if err := a.send(c2sHeartbeat); err != nil {
				return fmt.Errorf("heartbeat: %w", err)
			}
			lastHB = time.Now()
		}
		if time.Since(lastRx) > 5*time.Second {
			return errors.New("server silent 5s")
		}
		pfd := []unix.PollFd{{Fd: int32(fd), Events: unix.POLLIN}}
		n, err := unix.Poll(pfd, int(heartbeatEvery/time.Millisecond)/2)
		if err != nil && err != unix.EINTR {
			return err
		}
		if n <= 0 {
			continue
		}
		rn, oobn, _, _, err := unix.Recvmsg(fd, buf, oob, unix.MSG_DONTWAIT)
		if err != nil {
			if err == unix.EAGAIN {
				continue
			}
			return fmt.Errorf("recv: %w", err)
		}
		lastRx = time.Now()
		if rn < 16 {
			continue
		}
		id := binary.LittleEndian.Uint32(buf[0:])
		switch id {
		case s2cBuffer:
			size := int(binary.LittleEndian.Uint32(buf[16:]))
			mfd := -1
			if msgs, err := unix.ParseSocketControlMessage(oob[:oobn]); err == nil {
				for _, m := range msgs {
					if fds, err := unix.ParseUnixRights(&m); err == nil && len(fds) > 0 {
						mfd = fds[0]
					}
				}
			}
			if mfd < 0 || size <= 0 {
				return errors.New("buffer message without fd")
			}
			mem, err := unix.Mmap(mfd, 0, size, unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
			unix.Close(mfd) // the mapping keeps the buffer alive
			if err != nil {
				return fmt.Errorf("mmap %d: %w", size, err)
			}
			a.mu.Lock()
			if a.mem != nil {
				_ = unix.Munmap(a.mem)
			}
			a.mem = mem
			a.lastID = 0
			a.mu.Unlock()
		case s2cStatus:
			switch buf[16] {
			case c2sRegister:
				a.mu.Lock()
				a.status = "idle"
				start := !a.started
				a.started = true
				a.mu.Unlock()
				if start {
					if err := a.send(c2sStart); err != nil {
						return err
					}
				}
			case c2sStart:
				a.mu.Lock()
				a.status = "running"
				a.Err = ""
				a.mu.Unlock()
			case c2sUnregister:
				return errors.New("server unregistered us")
			}
		case s2cHeartbeat:
		}
	}
}

func u32p(mem []byte, off int) *uint32 { return (*uint32)(unsafe.Pointer(&mem[off])) }

// Latest copies the newest frame not returned before.
func (a *Anki) Latest() (*Frame, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	mem := a.mem
	if mem == nil || len(mem) < 64 {
		return nil, ErrNoFrame
	}
	if string(mem[0:4]) != "CAM0" {
		return nil, errors.New("camera: bad buffer magic")
	}
	w := atomic.LoadUint32(u32p(mem, 4))
	if w >= maxFrames {
		return nil, ErrNoFrame
	}
	frameSize := int(binary.LittleEndian.Uint32(mem[36:]))
	lock := u32p(mem, 8+4*int(w))
	if !atomic.CompareAndSwapUint32(lock, 0, 1) {
		return nil, ErrNoFrame // server writing this slot right now
	}
	defer atomic.StoreUint32(lock, 0)
	off := int(binary.LittleEndian.Uint32(mem[40+4*int(w):]))
	if off <= 0 || off+frameHdrLen > len(mem) {
		return nil, errors.New("camera: bad slot offset")
	}
	h := mem[off:]
	f := &Frame{
		Timestamp: binary.LittleEndian.Uint64(h[0:]),
		ID:        binary.LittleEndian.Uint32(h[8:]),
		W:         int(binary.LittleEndian.Uint32(h[12:])),
		H:         int(binary.LittleEndian.Uint32(h[16:])),
		Stride:    int(binary.LittleEndian.Uint32(h[20:])),
		Format:    h[25],
	}
	if f.Timestamp == 0 || f.ID == a.lastID {
		return nil, ErrNoFrame
	}
	n := f.Stride * f.H
	if n <= 0 || frameHdrLen+n > frameSize || off+frameHdrLen+n > len(mem) {
		return nil, fmt.Errorf("camera: frame %dx%d stride %d does not fit slot %d", f.W, f.H, f.Stride, frameSize)
	}
	f.Data = append([]byte(nil), h[frameHdrLen:frameHdrLen+n]...)
	a.lastID = f.ID
	a.Frames++
	return f, nil
}
