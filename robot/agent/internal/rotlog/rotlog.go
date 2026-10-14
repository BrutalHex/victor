// Package rotlog is a size-capped append log: path plus one rotated
// path.1, each at most Max/2 bytes, so the pair never exceeds Max. Writes are
// buffered and flushed at most every Flush (one open file, no per-line
// open/close on eMMC).
package rotlog

import (
	"bufio"
	"os"
	"sync"
	"time"
)

const DefaultMax = 1 << 20 // 1 MiB total, both files

type Log struct {
	Path  string
	Max   int64         // total cap for path + path.1
	Flush time.Duration // max buffering delay

	mu      sync.Mutex
	f       *os.File
	w       *bufio.Writer
	size    int64
	flushed time.Time
	now     func() time.Time
}

func New(path string) *Log {
	return &Log{Path: path, Max: DefaultMax, Flush: 2 * time.Second}
}

func (l *Log) clock() time.Time {
	if l.now != nil {
		return l.now()
	}
	return time.Now()
}

func (l *Log) half() int64 {
	if l.Max <= 0 {
		return DefaultMax / 2
	}
	return l.Max / 2
}

// open opens path for append. An oversized file left by an older agent (or
// any crash) is rotated away first, so the cap holds from the first write.
func (l *Log) open() error {
	if st, err := os.Stat(l.Path); err == nil && st.Size() >= l.half() {
		if err := l.rotateFile(); err != nil {
			return err
		}
	}
	f, err := os.OpenFile(l.Path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return err
	}
	l.f, l.w, l.size = f, bufio.NewWriterSize(f, 16<<10), st.Size()
	return nil
}

// rotateFile keeps only the newest half of an oversized path as path.1
// (a 167 MB leftover becomes ≤ Max/2), then starts path empty.
func (l *Log) rotateFile() error {
	old := l.Path + ".1"
	st, err := os.Stat(l.Path)
	if err != nil {
		return err
	}
	if st.Size() <= l.half() {
		return os.Rename(l.Path, old)
	}
	src, err := os.Open(l.Path)
	if err != nil {
		return err
	}
	defer src.Close()
	keep := l.half()
	buf := make([]byte, keep)
	n, err := src.ReadAt(buf, st.Size()-keep)
	if err != nil && n == 0 {
		return err
	}
	buf = buf[:n]
	// start on a whole line
	for i, b := range buf {
		if b == '\n' {
			buf = buf[i+1:]
			break
		}
	}
	tmp := old + ".tmp"
	if err := os.WriteFile(tmp, buf, 0644); err != nil {
		return err
	}
	if err := os.Rename(tmp, old); err != nil {
		return err
	}
	return os.Remove(l.Path)
}

func (l *Log) closeLocked() {
	if l.f == nil {
		return
	}
	_ = l.w.Flush()
	_ = l.f.Close()
	l.f, l.w = nil, nil
}

// WriteLine appends one line (it should end in '\n').
func (l *Log) WriteLine(line string) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.f == nil {
		if err := l.open(); err != nil {
			return err
		}
	}
	if l.size+int64(len(line)) > l.half() {
		l.closeLocked()
		if err := os.Rename(l.Path, l.Path+".1"); err != nil && !os.IsNotExist(err) {
			return err
		}
		if err := l.open(); err != nil {
			return err
		}
	}
	n, err := l.w.WriteString(line)
	l.size += int64(n)
	if now := l.clock(); now.Sub(l.flushed) >= l.Flush {
		_ = l.w.Flush()
		l.flushed = now
	}
	return err
}

// Close flushes and closes.
func (l *Log) Close() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.closeLocked()
}
