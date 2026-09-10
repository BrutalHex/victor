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

const Magic = "VHB1"

type Msg struct {
	Type uint8 // 1 robot, 2 hub
	Seq  uint32
	Tns  uint64
	Veto uint8
	Kind skill.Kind
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

func (cl *Client) Tick(veto uint8) {
	cl.mu.Lock()
	defer cl.mu.Unlock()
	if cl.c == nil {
		c, err := net.DialTimeout("tcp", cl.Addr, 80*time.Millisecond)
		if err != nil {
			return
		}
		cl.c = c
	}
	seq := cl.seq
	cl.seq++
	msg := Encode(Msg{Type: 1, Seq: seq, Tns: uint64(time.Now().UnixNano()), Veto: veto})
	_ = cl.c.SetDeadline(time.Now().Add(80 * time.Millisecond))
	if _, err := cl.c.Write(msg); err != nil {
		_ = cl.c.Close()
		cl.c = nil
		return
	}
	buf := make([]byte, 18)
	if _, err := io.ReadFull(cl.c, buf); err != nil {
		_ = cl.c.Close()
		cl.c = nil
		return
	}
	got, err := Decode(buf)
	if err != nil || got.Type != 2 {
		return
	}
	cl.lastOK.Store(time.Now().UnixNano())
	cl.kind.Store(uint32(got.Kind))
}

func (cl *Client) Close() {
	cl.mu.Lock()
	defer cl.mu.Unlock()
	if cl.c != nil {
		_ = cl.c.Close()
		cl.c = nil
	}
}
