// Command ble-bootstrap wraps digital-dream-labs/vector-bluetooth (RTS).
// Verbs: scan, connect, wifi-scan, wifi-connect, wifi-ip, ota-start,
// ota-cancel, get-status, first-flash.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/digital-dream-labs/vector-bluetooth/ble"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd := os.Args[1]
	args := os.Args[2:]
	if err := run(cmd, args); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintf(os.Stderr, `usage: ble-bootstrap <verb> [flags]

verbs:
  scan
  connect       --name Vector-XXXX --pin NNNNNN
  wifi-scan     (requires connect flags)
  wifi-connect  --ssid SSID --password PASS [--authtype 6]
  wifi-ip
  ota-start     --url http://host/file.ota
  ota-cancel
  get-status
  first-flash   --pin --ssid --password --url

Do not use Chrome Web Bluetooth as the shipping tool. Recovery OTA must be HTTP.
`)
}

func run(cmd string, args []string) error {
	fs := flag.NewFlagSet(cmd, flag.ContinueOnError)
	name := fs.String("name", env("VECTOR_BLE_NAME", ""), "BLE advertisement Vector-XXXX")
	pin := fs.String("pin", env("VECTOR_BLE_PIN", ""), "face PIN")
	id := fs.Int("id", -1, "scan index from `scan`")
	ssid := fs.String("ssid", env("WIFI_SSID", ""), "2.4 GHz SSID")
	pass := fs.String("password", env("WIFI_PASSWORD", ""), "Wi-Fi password")
	auth := fs.Int("authtype", atoi(env("WIFI_AUTHTYPE", "6"), 6), "Wi-Fi auth type (6=WPA2)")
	url := fs.String("url", env("OTA_URL", ""), "HTTP OTA URL")
	envFile := fs.String("env-out", ".env", "write ROBOT_SSH_IP here after wifi-ip")
	if err := fs.Parse(args); err != nil {
		return err
	}

	needLink := map[string]bool{
		"connect": true, "wifi-scan": true, "wifi-connect": true,
		"wifi-ip": true, "ota-start": true, "ota-cancel": true,
		"get-status": true, "first-flash": true,
	}

	switch cmd {
	case "scan":
		v, err := ble.New()
		if err != nil {
			return err
		}
		defer v.Close()
		r, err := v.Scan()
		if err != nil {
			return err
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(r)
	}

	if !needLink[cmd] {
		return fmt.Errorf("unknown verb %q", cmd)
	}

	v, err := ble.New()
	if err != nil {
		return err
	}
	defer v.Close()

	if err := connect(v, *name, *id, *pin); err != nil {
		return err
	}

	switch cmd {
	case "connect":
		fmt.Println("connected")
		return nil
	case "wifi-scan":
		r, err := v.WifiScan()
		if err != nil {
			return err
		}
		return dump(r)
	case "wifi-connect":
		if *ssid == "" {
			return fmt.Errorf("missing --ssid")
		}
		r, err := v.WifiConnect(*ssid, *pass, 45, *auth)
		if err != nil {
			return err
		}
		return dump(r)
	case "wifi-ip":
		r, err := v.WifiIP()
		if err != nil {
			return err
		}
		if err := dump(r); err != nil {
			return err
		}
		return writeIP(*envFile, r.IPv4)
	case "ota-start":
		if *url == "" || strings.HasPrefix(*url, "https://") {
			return fmt.Errorf("ota-start requires an HTTP (not HTTPS) --url")
		}
		r, err := v.OTAStart(*url)
		if err != nil {
			return err
		}
		return dump(r)
	case "ota-cancel":
		_, err := v.OTACancel()
		return err
	case "get-status":
		r, err := v.GetStatus()
		if err != nil {
			return err
		}
		return dump(r)
	case "first-flash":
		if *ssid == "" || *url == "" {
			return fmt.Errorf("first-flash requires --ssid and HTTP --url")
		}
		if strings.HasPrefix(*url, "https://") {
			return fmt.Errorf("recovery cannot pull HTTPS")
		}
		if _, err := v.WifiConnect(*ssid, *pass, 45, *auth); err != nil {
			return fmt.Errorf("wifi-connect: %w", err)
		}
		ip, err := v.WifiIP()
		if err != nil {
			return fmt.Errorf("wifi-ip: %w", err)
		}
		fmt.Printf("wifi ip %s\n", ip.IPv4)
		if err := writeIP(*envFile, ip.IPv4); err != nil {
			return err
		}
		if _, err := v.OTAStart(*url); err != nil {
			return fmt.Errorf("ota-start: %w", err)
		}
		fmt.Println("ota started; robot will reboot. probe SSH after it answers.")
		return nil
	default:
		return fmt.Errorf("unknown verb %q", cmd)
	}
}

func connect(v *ble.VectorBLE, name string, id int, pin string) error {
	if pin == "" {
		return fmt.Errorf("missing --pin")
	}
	r, err := v.Scan()
	if err != nil {
		return err
	}
	if id < 0 {
		if name == "" {
			return fmt.Errorf("pass --name Vector-XXXX or --id from scan")
		}
		id = -1
		for _, d := range r.Devices {
			if strings.EqualFold(d.Name, name) || strings.Contains(strings.ToLower(d.Name), strings.ToLower(name)) {
				id = d.ID
				break
			}
		}
		if id < 0 {
			return fmt.Errorf("device %q not advertising (is it on charger, double-press?)", name)
		}
	}
	if err := v.Connect(id); err != nil {
		return err
	}
	return v.SendPin(pin)
}

func dump(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func writeIP(envPath, ipv4 string) error {
	if ipv4 == "" || ipv4 == "0.0.0.0" {
		return fmt.Errorf("empty ipv4")
	}
	if net.ParseIP(ipv4) == nil {
		return fmt.Errorf("bad ipv4 %q", ipv4)
	}
	if envPath == "" {
		return nil
	}
	var body string
	b, err := os.ReadFile(envPath)
	if err == nil {
		body = string(b)
	}
	line := "ROBOT_SSH_IP=" + ipv4
	if strings.Contains(body, "ROBOT_SSH_IP=") {
		var out []string
		for _, l := range strings.Split(body, "\n") {
			if strings.HasPrefix(l, "ROBOT_SSH_IP=") {
				out = append(out, line)
			} else {
				out = append(out, l)
			}
		}
		body = strings.Join(out, "\n")
	} else {
		if body != "" && !strings.HasSuffix(body, "\n") {
			body += "\n"
		}
		body += line + "\n"
	}
	if err := os.MkdirAll(filepath.Dir(envPath), 0755); err != nil && filepath.Dir(envPath) != "." {
		return err
	}
	return os.WriteFile(envPath, []byte(body), 0600)
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func atoi(s string, def int) int {
	n, err := strconv.Atoi(s)
	if err != nil {
		return def
	}
	return n
}


