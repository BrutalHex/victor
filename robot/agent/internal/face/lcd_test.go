package face

import "testing"

func TestPanelWakeLeavesGeometry(t *testing.T) {
	cmds := panelWakeCmds()
	if len(cmds) < 2 {
		t.Fatalf("wake %v", cmds)
	}
	if cmds[0] != cmdSLPOUT || cmds[len(cmds)-1] != cmdDISPON {
		t.Fatalf("wake %v", cmds)
	}
	for _, c := range cmds {
		switch c {
		case 0x01, cmdCOLMOD, cmdMADCTL:
			t.Fatalf("wake reprograms the controller with 0x%02x", c)
		}
	}
}
