package telem

import (
	"fmt"
	"net"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/rotlog"
	"github.com/BrutalHex/victor/robot/agent/internal/vct1"
)

type Hub struct {
	Host       string
	SensorPort int
	AudioPort  int
	VideoPort  int
	GRPCPort   int
	LogPath    string

	seq     uint32
	aseq    uint32
	vseq    uint32
	lastOK  atomic.Int64
	udp     *net.UDPConn
	aud     *net.UDPConn
	vid     *net.UDPConn
	mu      sync.Mutex
	log     *rotlog.Log
	lastLog time.Time
}

// LogEvery is the telemetry.log line interval: the control loop sends a
// sensor packet every 20 ms, the local log keeps 2 lines/s (≈1.5 h in the
// 1 MiB cap) so it stays a "telemetry survives SSH off" trace, not a firehose.
const LogEvery = 500 * time.Millisecond

func (h *Hub) LastOK() time.Time {
	n := h.lastOK.Load()
	if n == 0 {
		return time.Time{}
	}
	return time.Unix(0, n)
}

func (h *Hub) HeartbeatMissing(d time.Duration) bool {
	t := h.LastOK()
	if t.IsZero() {
		return true
	}
	return time.Since(t) > d
}

func (h *Hub) Start() error {
	if h.SensorPort == 0 {
		h.SensorPort = 7502
	}
	if h.GRPCPort == 0 {
		h.GRPCPort = 7443
	}
	if h.AudioPort == 0 {
		h.AudioPort = 7501
	}
	if h.VideoPort == 0 {
		h.VideoPort = 7500
	}
	if h.Host == "" {
		h.Host = "robot.mohammadabbasi.com"
	}
	addr, err := net.ResolveUDPAddr("udp", fmt.Sprintf("%s:%d", h.Host, h.SensorPort))
	if err != nil {
		return err
	}
	c, err := net.DialUDP("udp", nil, addr)
	if err != nil {
		return err
	}
	h.udp = c
	if a, err := net.DialUDP("udp", nil, &net.UDPAddr{IP: addr.IP, Port: h.AudioPort}); err == nil {
		h.aud = a
	}
	if v, err := net.DialUDP("udp", nil, &net.UDPAddr{IP: addr.IP, Port: h.VideoPort}); err == nil {
		h.vid = v
	}
	if h.LogPath != "" {
		_ = os.MkdirAll(filepath.Dir(h.LogPath), 0755)
		h.log = rotlog.New(h.LogPath)
	}
	return nil
}

func (h *Hub) NoteOK() {
	h.lastOK.Store(time.Now().UnixNano())
}

func (h *Hub) SendSensor(s vct1.Sensor) []byte {
	seq := atomic.AddUint32(&h.seq, 1)
	buf := vct1.Encode(vct1.Header{
		Type: vct1.TypeSensor,
		Seq:  seq,
		Tns:  vct1.NowTns(),
	}, s.Marshal())
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.log != nil && time.Since(h.lastLog) >= LogEvery {
		h.lastLog = time.Now()
		line := fmt.Sprintf("%d seq=%d batt=%d charger=%d flags=%d lift=%d cliffs=%d,%d,%d,%d prox=%d\n",
			time.Now().UnixNano(), seq, s.BattMV, s.ChargerMV, s.Flags, s.EncLift,
			s.Cliffs[0], s.Cliffs[1], s.Cliffs[2], s.Cliffs[3], s.ProxMM)
		_ = h.log.WriteLine(line)
	}
	if h.udp != nil {
		_, _ = h.udp.Write(buf)
	}
	return buf
}

func (h *Hub) SendAudio(pcm []byte) []byte {
	if len(pcm) == 0 {
		return nil
	}
	seq := atomic.AddUint32(&h.aseq, 1)
	buf := vct1.EncodeAudio(seq, pcm)
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.aud != nil {
		_, _ = h.aud.Write(buf)
	}
	return buf
}

func (h *Hub) SendVideo(jpeg []byte, flags uint8) []byte {
	if len(jpeg) == 0 {
		return nil
	}
	seq := atomic.AddUint32(&h.vseq, 1)
	buf := vct1.EncodeVideo(seq, jpeg, flags)
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.vid != nil {
		_, _ = h.vid.Write(buf)
	}
	return buf
}
