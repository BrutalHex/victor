// Package statusfile writes small /data/victor status files without hitting
// the eMMC every control tick: a file is rewritten only when its content
// changed and at least Min has passed, or after Max to keep mtimes fresh.
package statusfile

import (
	"os"
	"sync"
	"time"
)

type Writer struct {
	Min, Max time.Duration
	mu       sync.Mutex
	last     map[string]entry
	write    func(string, []byte) error // tests
	now      func() time.Time
}

type entry struct {
	data string
	at   time.Time
}

func New() *Writer { return &Writer{Min: 500 * time.Millisecond, Max: 5 * time.Second} }

// Put reports whether the file was written.
func (w *Writer) Put(path, data string) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	now := time.Now()
	if w.now != nil {
		now = w.now()
	}
	if w.last == nil {
		w.last = map[string]entry{}
	}
	e, ok := w.last[path]
	if ok {
		age := now.Sub(e.at)
		if age < w.Min || (e.data == data && age < w.Max) {
			return false
		}
	}
	wr := w.write
	if wr == nil {
		wr = func(p string, b []byte) error { return os.WriteFile(p, b, 0644) }
	}
	if err := wr(path, []byte(data)); err != nil {
		return false
	}
	w.last[path] = entry{data, now}
	return true
}
