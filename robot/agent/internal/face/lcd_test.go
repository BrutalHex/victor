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

func TestMidasInitMatchesStock(t *testing.T) {
	steps := midasInit()
	if steps[0].cmd != 0x01 || steps[1].cmd != 0x11 {
		t.Fatalf("init must reset then sleep-out: %+v", steps[:2])
	}
	var sawOn bool
	for _, st := range steps {
		switch st.cmd {
		case cmdMADCTL:
			if len(st.data) != 1 || st.data[0] != 0xA8 {
				t.Fatalf("madctl %x", st.data)
			}
		case cmdDISPON:
			sawOn = true
		case cmdRASET:
			if got := window(rowShift, Height); string(st.data) != string(got) || got[1] != 24 || got[3] != 103 {
				t.Fatalf("raset %v", st.data)
			}
		}
	}
	if !sawOn {
		t.Fatal("no DISPON")
	}
}
