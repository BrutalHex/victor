package cliffcal

import (
	"encoding/json"
	"os"
	"path/filepath"
)

const Path = "/data/victor/cliffs.cal"

type Cal struct {
	Floor  [4]uint16 `json:"floor"`
	Thresh [4]uint16 `json:"thresh"`
	N      int       `json:"n"`
	sum    [4]uint64
}

const minFloor = 80
const need = 50

func (c *Cal) Add(cliffs [4]uint16) {
	for i := 0; i < 4; i++ {
		c.sum[i] += uint64(cliffs[i])
	}
	c.N++
	if c.N < need {
		return
	}
	for i := 0; i < 4; i++ {
		c.Floor[i] = uint16(c.sum[i] / uint64(c.N))
		c.Thresh[i] = uint16(float64(c.Floor[i]) * 0.40)
	}
}

func (c *Cal) Ready() bool {
	if c.N < need {
		return false
	}
	for i := 0; i < 2; i++ {
		if c.Floor[i] < minFloor {
			return false
		}
	}
	return true
}

func (c *Cal) Save(path string) error {
	if path == "" {
		path = Path
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0644)
}

func Load(path string) (*Cal, error) {
	if path == "" {
		path = Path
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return &Cal{}, err
	}
	c := &Cal{}
	if err := json.Unmarshal(b, c); err != nil {
		return &Cal{}, err
	}
	c.N = need
	return c, nil
}
