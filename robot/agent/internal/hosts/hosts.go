// Package hosts maintains the managed /etc/hosts block for the hub hostname.
package hosts

import (
	"os"
	"strings"
)

const (
	Begin = "# managed-by: victor-agent"
	End   = "# end-managed-by: victor-agent"
	Name  = "robot.mohammadabbasi.com"
)

func Block(ip, name string) string {
	if name == "" {
		name = Name
	}
	return Begin + "\n" + ip + "    " + name + " hub\n" + End + "\n"
}

func Rewrite(contents, ip, name string) string {
	lines := strings.Split(strings.ReplaceAll(contents, "\r\n", "\n"), "\n")
	var out []string
	skip := false
	for _, line := range lines {
		if strings.HasPrefix(line, Begin) {
			skip = true
			continue
		}
		if skip {
			if strings.HasPrefix(line, End) {
				skip = false
			}
			continue
		}
		out = append(out, line)
	}
	for len(out) > 0 && out[len(out)-1] == "" {
		out = out[:len(out)-1]
	}
	body := strings.Join(out, "\n")
	if body != "" && !strings.HasSuffix(body, "\n") {
		body += "\n"
	}
	return body + Block(ip, name)
}

func Apply(path, ip, name string) error {
	if ip == "" {
		return nil
	}
	if name == "" {
		name = Name
	}
	b, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return os.WriteFile(path, []byte(Rewrite(string(b), ip, name)), 0644)
}
