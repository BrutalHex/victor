package camera

import (
	"encoding/binary"
	"path/filepath"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

// fakeServer mimics mm-anki-camera: REGISTER -> BUFFER(fd)+STATUS, START -> STATUS.
func fakeServer(t *testing.T, path string, w, h int) (stop func()) {
	t.Helper()
	fd, err := unix.Socket(unix.AF_UNIX, unix.SOCK_DGRAM, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := unix.Bind(fd, &unix.SockaddrUnix{Name: path}); err != nil {
		t.Fatal(err)
	}
	stride := w * 3
	frameLen := (frameHdrLen + stride*h + 63) &^ 63
	hdrLen := 64
	size := (hdrLen + frameLen*maxFrames + 4095) &^ 4095
	mfd, err := unix.MemfdCreate("cam", 0)
	if err != nil {
		t.Skip("memfd:", err)
	}
	_ = unix.Ftruncate(mfd, int64(size))
	mem, err := unix.Mmap(mfd, 0, size, unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
	if err != nil {
		t.Fatal(err)
	}
	copy(mem, "CAM0")
	binary.LittleEndian.PutUint32(mem[32:], maxFrames)
	binary.LittleEndian.PutUint32(mem[36:], uint32(frameLen))
	for i := 0; i < maxFrames; i++ {
		off := hdrLen + frameLen*i
		binary.LittleEndian.PutUint32(mem[40+4*i:], uint32(off))
		fh := mem[off:]
		binary.LittleEndian.PutUint32(fh[12:], uint32(w))
		binary.LittleEndian.PutUint32(fh[16:], uint32(h))
		binary.LittleEndian.PutUint32(fh[20:], uint32(stride))
		fh[24] = 24
		fh[25] = FormatRGB888
	}
	// write frame id 7 into slot 2: red-ish gradient
	off := hdrLen + frameLen*2
	binary.LittleEndian.PutUint64(mem[off:], 12345)
	binary.LittleEndian.PutUint32(mem[off+8:], 7)
	for i := 0; i < w*h; i++ {
		mem[off+frameHdrLen+3*i] = 200
		mem[off+frameHdrLen+3*i+1] = 100
		mem[off+frameHdrLen+3*i+2] = 50
	}
	binary.LittleEndian.PutUint32(mem[4:], 2)

	done := make(chan struct{})
	go func() {
		buf := make([]byte, msgSize)
		for {
			select {
			case <-done:
				return
			default:
			}
			_ = unix.SetsockoptTimeval(fd, unix.SOL_SOCKET, unix.SO_RCVTIMEO, &unix.Timeval{Usec: 100000})
			n, from, err := unix.Recvfrom(fd, buf, 0)
			if err != nil || n < 4 {
				continue
			}
			reply := func(id uint32, ack byte, rights []byte) {
				m := encodeMsg(id, nil)
				if id == s2cBuffer {
					binary.LittleEndian.PutUint32(m[16:], uint32(size))
				} else {
					m[16] = ack
				}
				_ = unix.Sendmsg(fd, m, rights, from, 0)
			}
			switch binary.LittleEndian.Uint32(buf) {
			case c2sRegister:
				reply(s2cBuffer, 0, unix.UnixRights(mfd))
				reply(s2cStatus, c2sRegister, nil)
			case c2sStart:
				reply(s2cStatus, c2sStart, nil)
			case c2sHeartbeat:
				reply(s2cHeartbeat, 0, nil)
			}
		}
	}()
	return func() { close(done); unix.Close(fd) }
}

func TestAnkiClientGetsFrame(t *testing.T) {
	dir := t.TempDir()
	ServerSock = filepath.Join(dir, "srv")
	ClientSock = filepath.Join(dir, "cli")
	stop := fakeServer(t, ServerSock, 64, 36)
	defer stop()
	a := &Anki{}
	quit := make(chan struct{})
	go a.Run(quit)
	defer close(quit)
	var f *Frame
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		var err error
		if f, err = a.Latest(); err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if f == nil {
		st, _, e := a.Status()
		t.Fatalf("no frame; status=%s err=%s", st, e)
	}
	if f.ID != 7 || f.W != 64 || f.H != 36 || f.Format != FormatRGB888 || f.Data[0] != 200 || f.Data[2] != 50 {
		t.Fatalf("frame %+v", f)
	}
	if _, err := a.Latest(); err != ErrNoFrame {
		t.Fatalf("same frame twice: %v", err)
	}
	if st, _, _ := a.Status(); st != "running" {
		t.Fatalf("status %s", st)
	}
	// slot lock released
	a.mu.Lock()
	lock := *u32p(a.mem, 8+4*2)
	a.mu.Unlock()
	if lock != 0 {
		t.Fatal("slot left locked")
	}
	tn := &Tone{Gamma: 1}
	img, err := tn.ToRGBA(f, 2)
	if err != nil || img.Bounds().Dx() != 32 || img.Bounds().Dy() != 18 {
		t.Fatalf("toRGBA %v %v", err, img.Bounds())
	}
	// gray-world balances a uniform colour cast to neutral
	p := img.Pix[:3]
	if d := int(p[0]) - int(p[2]); d > 3 || d < -3 {
		t.Fatalf("white balance %v", p)
	}
	tn2 := &Tone{Gamma: 1, SwapRB: true}
	if _, err := tn2.ToRGBA(f, 1); err != nil {
		t.Fatal(err)
	}
}

func TestHalveAndFlip(t *testing.T) {
	f := &Frame{W: 4, H: 2, Stride: 12, Format: FormatRGB888, Data: make([]byte, 24)}
	f.Data[0] = 255 // top-left red-ish
	tn := &Tone{Gamma: 1, Flip: true}
	img, err := tn.ToRGBA(f, 1)
	if err != nil {
		t.Fatal(err)
	}
	if img.Pix[img.PixOffset(3, 1)] == 0 {
		t.Fatal("flip: top-left pixel should land bottom-right")
	}
	h := halve(img)
	if h.Bounds().Dx() != 2 || h.Bounds().Dy() != 1 {
		t.Fatal("halve size")
	}
}
