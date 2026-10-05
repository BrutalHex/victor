package camera

import (
	"errors"
	"os"
)

// grabV4L2 is a best-effort snapshot. Qualcomm preview often needs STREAMING
// mmap; when that fails we leave camera optional — the safety loop must not die.
// TODO(hardware): mmap REQBUFS path once the vendor node is owned by victor-agent.
func grabV4L2(dev string) ([]byte, error) {
	f, err := os.Open(dev)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	buf := make([]byte, 256*1024)
	n, err := f.Read(buf)
	if err != nil {
		return nil, err
	}
	if n < 64 || buf[0] != 0xff || buf[1] != 0xd8 {
		return nil, errors.New("v4l2: not a JPEG snapshot")
	}
	return buf[:n], nil
}
