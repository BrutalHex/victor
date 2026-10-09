package face

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/binary"
	"io"
	"sync"
	"time"
)

// Digital Dream Labs' knowledge-graph "searching" face: small squares linked
// into a network that shift and reconnect. Stock Vector shows it while it
// waits on the cloud. Frames come from vector-animations-build
// (face_knowledgegraph_searching_getin + face_knowledgegraph_searching),
// packed for 160x80 by tools/ddl_sprites.py; DDL Software Asset License 1.0.
//
//go:embed ddl/knowledgegraph_searching.gray.gz
var searchingGz []byte

// SpriteFPS is the engine's sprite-sequence rate (ANIM_TIME_STEP 33 ms).
const SpriteFPS = 30

var (
	searchingOnce  sync.Once
	searchingGetin [][]byte
	searchingLoop  [][]byte
	searchingLUT   [256]uint16
)

func loadSearching() {
	zr, err := gzip.NewReader(bytes.NewReader(searchingGz))
	if err != nil {
		return
	}
	raw, err := io.ReadAll(zr)
	if err != nil || len(raw) < 6 || string(raw[:4]) != "KGS1" {
		return
	}
	nIn, nLoop := int(raw[4]), int(raw[5])
	px := Width * Height
	if len(raw) != 6+(nIn+nLoop)*px || nLoop == 0 {
		return
	}
	for i := 0; i < nIn+nLoop; i++ {
		f := raw[6+i*px : 6+(i+1)*px]
		if i < nIn {
			searchingGetin = append(searchingGetin, f)
		} else {
			searchingLoop = append(searchingLoop, f)
		}
	}
	// The engine tints grayscale face sprites with the eye colour: hue and
	// saturation from the eyes, value from the sprite.
	for v := 0; v < 256; v++ {
		if v == 0 {
			continue
		}
		searchingLUT[v] = hsv(0.42, 1, float64(v)/255)
	}
}

// SearchingFrames reports the getin and loop lengths (0, 0 if unavailable).
func SearchingFrames() (int, int) {
	searchingOnce.Do(loadSearching)
	return len(searchingGetin), len(searchingLoop)
}

// SearchingFrame is the thinking face elapsed into the turn: the getin
// once, then the loop, at the stock 30 fps sprite timing. It replaces the
// eyes; nil if the asset failed to load.
func SearchingFrame(elapsed time.Duration) []byte {
	searchingOnce.Do(loadSearching)
	if len(searchingLoop) == 0 {
		return nil
	}
	if elapsed < 0 {
		elapsed = 0
	}
	idx := int(elapsed * SpriteFPS / time.Second)
	var g []byte
	if idx < len(searchingGetin) {
		g = searchingGetin[idx]
	} else {
		g = searchingLoop[(idx-len(searchingGetin))%len(searchingLoop)]
	}
	buf := make([]byte, Bytes)
	for i, v := range g {
		binary.BigEndian.PutUint16(buf[i*2:], searchingLUT[v])
	}
	return buf
}
