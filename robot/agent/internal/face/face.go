// Package face renders 184×96 RGB565 captions for CHARGE-LATCH.
package face

import (
	"encoding/binary"
	"os"
	"strings"
)

const (
	Width  = 184
	Height = 96
	Bytes  = Width * Height * 2
)

func RGB565(r, g, b uint8) uint16 {
	return (uint16(r>>3) << 11) | (uint16(g>>2) << 5) | uint16(b>>3)
}

var (
	Green = RGB565(16, 220, 48)
	Red   = RGB565(220, 24, 24)
	Amber = RGB565(220, 180, 16)
	Black = RGB565(0, 0, 0)
	White = RGB565(255, 255, 255)
)

func Frame(text string, fg uint16) []byte {
	buf := make([]byte, Bytes)
	text = strings.ToUpper(strings.TrimSpace(text))
	scale := 3
	tw := len(text) * 6 * scale
	th := 7 * scale
	x0 := (Width - tw) / 2
	y0 := (Height - th) / 2
	if x0 < 0 {
		x0 = 2
	}
	if y0 < 0 {
		y0 = 2
	}
	for i, ch := range text {
		drawGlyph(buf, x0+i*6*scale, y0, ch, fg, scale)
	}
	return buf
}

func WriteDev(path string, frame []byte) error {
	if path == "" {
		return nil
	}
	return os.WriteFile(path, frame, 0644)
}

func drawGlyph(buf []byte, x, y int, ch rune, fg uint16, scale int) {
	g, ok := font5x7[ch]
	if !ok {
		g = font5x7['?']
	}
	for row := 0; row < 7; row++ {
		bits := g[row]
		for col := 0; col < 5; col++ {
			if bits&(1<<(4-col)) == 0 {
				continue
			}
			for dy := 0; dy < scale; dy++ {
				for dx := 0; dx < scale; dx++ {
					px := x + col*scale + dx
					py := y + row*scale + dy
					if px < 0 || py < 0 || px >= Width || py >= Height {
						continue
					}
					off := (py*Width + px) * 2
					binary.LittleEndian.PutUint16(buf[off:], fg)
				}
			}
		}
	}
}

// 5x7 caps, bit4 = leftmost.
var font5x7 = map[rune][7]uint8{
	' ': {0, 0, 0, 0, 0, 0, 0},
	'A': {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'E': {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F},
	'F': {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10},
	'H': {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'N': {0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11},
	'O': {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
	'S': {0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E},
	'T': {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04},
	'U': {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
	'-': {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00},
	'?': {0x0E, 0x11, 0x01, 0x06, 0x04, 0x00, 0x04},
}
