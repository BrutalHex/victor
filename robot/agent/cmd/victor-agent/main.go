package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"net"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/anki"
	"github.com/BrutalHex/victor/robot/agent/internal/audio"
	"github.com/BrutalHex/victor/robot/agent/internal/blemask"
	"github.com/BrutalHex/victor/robot/agent/internal/camera"
	"github.com/BrutalHex/victor/robot/agent/internal/cliffcal"
	"github.com/BrutalHex/victor/robot/agent/internal/face"
	"github.com/BrutalHex/victor/robot/agent/internal/hosts"
	"github.com/BrutalHex/victor/robot/agent/internal/latch"
	"github.com/BrutalHex/victor/robot/agent/internal/link"
	"github.com/BrutalHex/victor/robot/agent/internal/simulate"
	"github.com/BrutalHex/victor/robot/agent/internal/skill"
	"github.com/BrutalHex/victor/robot/agent/internal/spine"
	"github.com/BrutalHex/victor/robot/agent/internal/sshctl"
	"github.com/BrutalHex/victor/robot/agent/internal/telem"
	"github.com/BrutalHex/victor/robot/agent/internal/vct1"
	"github.com/BrutalHex/victor/robot/agent/internal/veto"
)

func main() {
	if len(os.Args) > 1 && !strings.HasPrefix(os.Args[1], "-") {
		switch os.Args[1] {
		case "ssh-on":
			os.Exit(runSSH(true))
		case "ssh-off":
			os.Exit(runSSH(false))
		case "ssh-toggle":
			os.Exit(runToggle())
		case "ble-mask":
			if err := blemask.Apply(); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			fmt.Println(blemask.StatusLine())
			return
		case "status":
			printStatus()
			return
		case "latch-simulate":
			os.Exit(runSimulate())
		case "mask-anki":
			if err := anki.Mask(); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			fmt.Println("anki-robot.target stopped")
			return
		case "restore-anki":
			if err := anki.Restore(); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			fmt.Println("anki-robot.target started")
			return
		case "calibrate":
			_ = os.Remove(cliffcal.Path)
			fmt.Println("cliff calibration cleared; agent will recapture floor samples")
			return
		case "run":
			os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
		}
	}
	os.Exit(runDaemon())
}
