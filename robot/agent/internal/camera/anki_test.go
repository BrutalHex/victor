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

// raw10 builds a MIPI RAW10 BGGR frame; left half reddish, right half bluish.
func raw10(w, h int) *Frame {
	stride := w * 10 / 8
	d := make([]byte, stride*h)
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			var v byte
			red := x < w/2
			switch {
			case y%2 == 0 && x%2 == 0: // B
				v = 40
				if !red {
					v = 200
				}
			case y%2 == 1 && x%2 == 1: // R
				v = 200
				if !red {
					v = 40
				}
			default: // G
				v = 100
			}
			d[y*stride+x/4*5+x%4] = v
		}
	}
	return &Frame{W: w, H: h, Stride: stride, Format: FormatRAW2MP, Data: d}
}

func TestRawDebayerChannels(t *testing.T) {
	f := raw10(160, 120)
	tn := &Tone{Gamma: 1, Black: 0}
	img, err := tn.Convert(f)
	if err != nil {
		t.Fatal(err)
	}
	if img.Bounds().Dx() != 80 || img.Bounds().Dy() != 60 {
		t.Fatalf("size %v", img.Bounds())
	}
	l := img.Pix[img.PixOffset(10, 30):]
	r := img.Pix[img.PixOffset(70, 30):]
	if !(l[0] > l[2] && r[2] > r[0]) {
		t.Fatalf("channels: left %v right %v", l[:3], r[:3])
	}
}

func BenchmarkTick(b *testing.B) {
	f := raw10(1600, 1200)
	tn := &Tone{Gamma: 0.8, Black: 16}
	for i := 0; i < b.N; i++ {
		_ = tn.Prepare(f)
		_, _ = EncodeJPEG(tn.Render(f, NavW, NavH), 70)
		if i%2 == 0 {
			_, _ = EncodeJPEG(tn.Render(f, FaceW, FaceH), 75)
		}
	}
}

func TestAutoExposure(t *testing.T) {
	var sent [][2]float64
	e := &AE{Target: 0.3, ExpMs: 10, Gain: 1, send: func(ms uint16, g float32) error {
		sent = append(sent, [2]float64{float64(ms), float64(g)})
		return nil
	}}
	now := time.Now()
	e.step(0.05, now) // dark: exposure up (x2 per step)
	if len(sent) != 1 || sent[0][0] != 20 {
		t.Fatalf("dark step %v", sent)
	}
	e.step(0.05, now.Add(100*time.Millisecond)) // rate limited
	if len(sent) != 1 {
		t.Fatal("not rate limited")
	}
	for i := 1; i < 6; i++ {
		e.step(0.05, now.Add(time.Duration(i)*time.Second))
	}
	if e.ExpMs != 33 || e.Gain != 4 {
		t.Fatalf("limits exp=%v gain=%v", e.ExpMs, e.Gain)
	}
	n := len(sent)
	e.step(0.29, now.Add(10*time.Second)) // close enough: no change
	if len(sent) != n {
		t.Fatal("changed inside the dead band")
	}
	e.step(0.9, now.Add(11*time.Second)) // bright: halves
	if e.ExpMs*e.Gain > 33*4/2+0.1 {
		t.Fatalf("bright step exp=%v gain=%v", e.ExpMs, e.Gain)
	}
}

func TestWithLatestNoCopyAndMean(t *testing.T) {
	f := raw10(160, 120)
	tn := &Tone{Gamma: 1, Black: 0}
	if err := tn.Prepare(f); err != nil {
		t.Fatal(err)
	}
	if tn.Mean < 0.3 || tn.Mean > 0.6 {
		t.Fatalf("mean %v", tn.Mean)
	}
}

func BenchmarkPartsARM(b *testing.B) {
	f := raw10(1600, 1200)
	tn := &Tone{Gamma: 0.8, Black: 16, Target: 0.3}
	b.Run("prepare", func(b *testing.B) {
		for i := 0; i < b.N; i++ {
			_ = tn.Prepare(f)
		}
	})
	b.Run("render-nav", func(b *testing.B) {
		for i := 0; i < b.N; i++ {
			_ = tn.Prepare(f)
			tn.Render(f, NavW, NavH)
		}
	})
	b.Run("render-face", func(b *testing.B) {
		for i := 0; i < b.N; i++ {
			_ = tn.Prepare(f)
			tn.Render(f, FaceW, FaceH)
		}
	})
	nav := tn.Render(f, NavW, NavH)
	face := tn.Render(f, FaceW, FaceH)
	b.Run("jpeg-nav", func(b *testing.B) {
		for i := 0; i < b.N; i++ {
			EncodeJPEG(nav, 70)
		}
	})
	b.Run("jpeg-face", func(b *testing.B) {
		for i := 0; i < b.N; i++ {
			EncodeJPEG(face, 75)
		}
	})
}

func TestRawSwapAndFlipConsistent(t *testing.T) {
	f := raw10(160, 120)
	plain := &Tone{Gamma: 1}
	sw := &Tone{Gamma: 1, SwapRB: true}
	fl := &Tone{Gamma: 1, Flip: true}
	a, _ := plain.Convert(f)
	b, _ := sw.Convert(f)
	c, _ := fl.Convert(f)
	pa := a.Pix[a.PixOffset(10, 30):]
	pb := b.Pix[b.PixOffset(10, 30):]
	pc := c.Pix[c.PixOffset(69, 29):]
	if !(pa[0] > pa[2] && pb[2] > pb[0]) {
		t.Fatalf("swap: plain %v swapped %v", pa[:3], pb[:3])
	}
	if pa[0] != pc[0] || pa[2] != pc[2] {
		t.Fatalf("flip: %v vs %v", pa[:3], pc[:3])
	}
}
