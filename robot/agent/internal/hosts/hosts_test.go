package hosts

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRewriteKeepsOtherLines(t *testing.T) {
	in := "127.0.0.1 localhost\n# managed-by: victor-agent\n1.2.3.4 old\n# end-managed-by: victor-agent\n"
	got := Rewrite(in, "10.0.0.5", "robot.mohammadabbasi.com")
	if !strings.Contains(got, "127.0.0.1 localhost") {
		t.Fatal(got)
	}
	if strings.Contains(got, "1.2.3.4") {
		t.Fatal("old ip lingered")
	}
	if !strings.Contains(got, "10.0.0.5    robot.mohammadabbasi.com hub") {
		t.Fatal(got)
	}
}

func TestApply(t *testing.T) {
	p := filepath.Join(t.TempDir(), "hosts")
	if err := os.WriteFile(p, []byte("127.0.0.1 localhost\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := Apply(p, "192.168.1.8", ""); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(p)
	if !strings.Contains(string(b), "192.168.1.8    robot.mohammadabbasi.com hub") {
		t.Fatal(string(b))
	}
}
