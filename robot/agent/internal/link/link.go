// Package link is the robot→hub TCP heartbeat and skill channel on :7443.
package link

import (
	"encoding/binary"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/skill"
)

const (
	Magic     = "VHB1"
	TypeRobot = 1
	TypeHub   = 2
	TypeCmd   = 3
	TypeMedia = 5

	CmdFaceUI  uint8 = 1
	CmdSpeak   uint8 = 2
	CmdDisplay uint8 = 3

	maxMedia = 1 << 20
	maxCmd   = 2 << 20
)

type Msg struct {
	Type uint8 // 1 robot, 2 hub, 3 hub cmd, 5 robot media
	Seq  uint32
	Tns  uint64
	Veto uint8
	Kind skill.Kind
}

type Cmd struct {
	Kind    uint8
	Payload []byte
}

func EncodeCmd(seq uint32, tns uint64, kind uint8, payload []byte) []byte {
	hdr := Encode(Msg{Type: TypeCmd, Seq: seq, Tns: tns})
	hdr[17] = kind
	b := make([]byte, 18+4+len(payload))
	copy(b, hdr)
	binary.LittleEndian.PutUint32(b[18:22], uint32(len(payload)))
	copy(b[22:], payload)
	return b
}

func EncodeMedia(seq uint32, tns uint64, vct1 []byte) []byte {
	hdr := Encode(Msg{Type: TypeMedia, Seq: seq, Tns: tns})
	b := make([]byte, 18+4+len(vct1))
	copy(b, hdr)
	binary.LittleEndian.PutUint32(b[18:22], uint32(len(vct1)))
	copy(b[22:], vct1)
	return b
}

func Encode(m Msg) []byte {
	b := make([]byte, 18)
	copy(b[0:4], Magic)
	b[4] = m.Type
	binary.LittleEndian.PutUint32(b[5:9], m.Seq)
	binary.LittleEndian.PutUint64(b[9:17], m.Tns)
	b[17] = m.Veto
	if m.Type == 2 {
		b[17] = uint8(m.Kind)
	}
	return b
}

func Decode(b []byte) (Msg, error) {
	var m Msg
	if len(b) < 18 || string(b[0:4]) != Magic {
		return m, io.ErrUnexpectedEOF
	}
	m.Type = b[4]
	m.Seq = binary.LittleEndian.Uint32(b[5:9])
	m.Tns = binary.LittleEndian.Uint64(b[9:17])
	if m.Type == 2 {
		m.Kind = skill.Kind(b[17])
	} else {
		m.Veto = b[17]
	}
	return m, nil
}

type Client struct {
	Addr string

	mu     sync.Mutex
	c      net.Conn
	seq    uint32
	lastOK atomic.Int64
	kind   atomic.Uint32
	cmds   chan Cmd
	media  [][]byte
}

func (cl *Client) LastOK() time.Time {
	n := cl.lastOK.Load()
	if n == 0 {
		return time.Time{}
	}
	return time.Unix(0, n)
}

func (cl *Client) Skill() skill.Kind {
	return skill.Kind(cl.kind.Load())
}

func (cl *Client) Commands() <-chan Cmd {
	cl.mu.Lock()
	defer cl.mu.Unlock()
	if cl.cmds == nil {
		cl.cmds = make(chan Cmd, 8)
	}
	return cl.cmds
}

func (cl *Client) QueueMedia(vct1 []byte) {
	if len(vct1) == 0 {
		return
	}
	cl.mu.Lock()
	defer cl.mu.Unlock()
	n := 0
	for _, p := range cl.media {
		n += len(p)
	}
	if n+len(vct1) > maxMedia {
		if len(cl.media) > 0 {
			cl.media = cl.media[1:]
		}
	}
	cp := make([]byte, len(vct1))
	copy(cp, vct1)
	cl.media = append(cl.media, cp)
}

func (cl *Client) Tick(veto uint8) {
	cl.mu.Lock()
	defer cl.mu.Unlock()
	if cl.cmds == nil {
		cl.cmds = make(chan Cmd, 8)
	}
	if cl.c == nil {
		c, err := net.DialTimeout("tcp", cl.Addr, 80*time.Millisecond)
		if err != nil {
			return
		}
		cl.c = c
		go cl.readLoop(c)
	}
	seq := cl.seq
	cl.seq++
	msg := Encode(Msg{Type: TypeRobot, Seq: seq, Tns: uint64(time.Now().UnixNano()), Veto: veto})
	_ = cl.c.SetWriteDeadline(time.Now().Add(80 * time.Millisecond))
	if _, err := cl.c.Write(msg); err != nil {
		_ = cl.c.Close()
		cl.c = nil
		return
	}
	media := cl.media
	const maxFlush = 4
	if len(media) > maxFlush {
		cl.media = media[maxFlush:]
		media = media[:maxFlush]
	} else {
		cl.media = nil
	}
	for _, p := range media {
		seq = cl.seq
		cl.seq++
		frame := EncodeMedia(seq, uint64(time.Now().UnixNano()), p)
		_ = cl.c.SetWriteDeadline(time.Now().Add(40 * time.Millisecond))
		if _, err := cl.c.Write(frame); err != nil {
			_ = cl.c.Close()
			cl.c = nil
			return
		}
	}
}

func (cl *Client) readLoop(c net.Conn) {
	defer func() {
		cl.mu.Lock()
		if cl.c == c {
			_ = cl.c.Close()
			cl.c = nil
		}
		cl.mu.Unlock()
	}()
	for {
		_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
		buf := make([]byte, 18)
		if _, err := io.ReadFull(c, buf); err != nil {
			return
		}
		got, err := Decode(buf)
		if err != nil {
			return
		}
		switch got.Type {
		case TypeHub:
			cl.lastOK.Store(time.Now().UnixNano())
			cl.kind.Store(uint32(got.Kind))
		case TypeCmd:
			_ = c.SetReadDeadline(time.Now().Add(5 * time.Second))
			lnb := make([]byte, 4)
			if _, err := io.ReadFull(c, lnb); err != nil {
				return
			}
			n := binary.LittleEndian.Uint32(lnb)
			if n > maxCmd {
				return
			}
			payload := make([]byte, n)
			if n > 0 {
				if _, err := io.ReadFull(c, payload); err != nil {
					return
				}
			}
			cl.mu.Lock()
			ch := cl.cmds
			cl.mu.Unlock()
			if ch == nil {
				continue
			}
			select {
			case ch <- Cmd{Kind: buf[17], Payload: payload}:
			default:
			}
		}
	}
}

func (cl *Client) Close() {
	cl.mu.Lock()
	defer cl.mu.Unlock()
	if cl.c != nil {
		_ = cl.c.Close()
		cl.c = nil
	}
}
