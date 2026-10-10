#!/usr/bin/env bash
# After first-flash: check the robot booted OUR image with the GROK_INSTRUCTIONS
# first-boot state. Read-only except it prints; run from the build machine.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

robot_ssh 'fail=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }
cat /etc/victor-release 2>/dev/null || echo "FAIL: /etc/victor-release missing (not our image?)"
chk "slot $(tr " " "\n" </proc/cmdline | sed -n s/^androidboot.slot_suffix=//p)" true
chk "ssh.enabled=1" "grep -qx 1 /data/victor/ssh.enabled"
chk "ble.disabled flag" "test -f /data/victor/ble.disabled"
# SSH is toggled by voice only (owner request, 10 Oct 2026): the CHARGE-LATCH button gesture stays off
chk "button gesture off (no charge-latch.enabled)" "test ! -e /data/victor/charge-latch.enabled"
for u in ankibluetoothd vic-switchboard btproperty; do
  chk "$u masked/inactive" "! systemctl is-active -q $u.service"
done
if [ -e /dev/rfkill ]; then chk "bluetooth rfkill blocked" "! rfkill list bluetooth 2>/dev/null | grep -q \"Soft blocked: no\""; else echo "INFO: no /dev/rfkill on this kernel"; fi
chk "no HCI device up" "! ls /sys/class/bluetooth/hci* >/dev/null 2>&1 || ! grep -q 1 /sys/class/bluetooth/hci*/power 2>/dev/null"
S=$(tr " " "\n" </proc/cmdline | sed -n "s/^androidboot.slot_suffix=_//p")
chk "A/B slot $S marked successful" "/bin/bootctl-anki $S status $S | grep -q \"successful: 1\""
chk "victor-agent active" "systemctl is-active -q victor-agent.service"
chk "vic-anim stopped (agent owns the face)" "! systemctl is-active -q vic-anim.service"
# Face: settings must survive an agent restart and a reboot.
FS=$(systemctl show -p Environment victor-agent.service | sed "s/^Environment=//" | tr " " "\n" | sed -n "s/^VICTOR_FACE_SPI=//p")
FS=${FS:-/dev/spidev1.0}
chk "face.txt ready=true" "grep -q ready=true /data/victor/face.txt"
chk "face SPI is $FS (panel; spidev0.0 is the IMU)" "grep -q \"spi=$FS \" /data/victor/face.txt"
chk "face panel midas init, hw 0x20" "grep -q \"init=midas hw=0x20 \" /data/victor/face.txt"
chk "midas init logged this boot" "journalctl -b -u victor-agent --no-pager | grep -q \"midas init on $FS\""
chk "DDL thinking animation loaded (8+36 frames)" "grep -q think=ddl-searching:8+36 /data/victor/face.txt"
chk "face backlight on" "[ \$(cat /sys/class/leds/face-backlight-left/brightness) -gt 0 ]"
chk "vic-bootAnim stopped" "! systemctl is-active -q vic-bootAnim.service"
chk "no spi0.0 unsupported mode bits since boot" "! dmesg | grep -q \"spi0.0: setup: unsupported mode bits\""
chk "agent drop-in on disk (/etc, survives reboot)" "grep -q \"ExecStart=/data/victor/victor-agent run\" /etc/systemd/system/victor-agent.service.d/10-sync.conf"
chk "running agent is /data/victor/victor-agent" "systemctl show -p ExecStart victor-agent.service | grep -q /data/victor/victor-agent"
# Inspect the binaries, never run them: an older agent treats an unknown
# subcommand as "run" and would start a second daemon.
chk "/data agent: spidev1.0 + DDL asset" "grep -aq /dev/spidev1.0 /data/victor/victor-agent && grep -aq victor-face-asset:ddl-knowledgegraph-searching /data/victor/victor-agent"
warn0() { if eval "$2" >/dev/null 2>&1; then echo "PASS: $1"; else echo "WARN: $1"; fi; }
warn0 "image /usr/bin/victor-agent has the same face defaults (else: next make ota)" "grep -aq /dev/spidev1.0 /usr/bin/victor-agent && grep -aq victor-face-asset:ddl-knowledgegraph-searching /usr/bin/victor-agent"
# telemetry.log: hard 1 MiB cap (current + .1)
T=$(( $(cat /data/victor/telemetry.log 2>/dev/null | wc -c) + $(cat /data/victor/telemetry.log.1 2>/dev/null | wc -c) ))
chk "telemetry.log + .1 = $T bytes <= 1 MiB" "[ $T -le 1048576 ]"
chk "telemetry still being written" "[ -s /data/victor/telemetry.log ] || [ -s /data/victor/telemetry.log.1 ]"
chk "mic gain levelled" "grep -q gain= /data/victor/mics.txt"
chk "/data mounted exec" "! grep -E \" /data \" /proc/mounts | grep -q noexec"
chk "hub hostname configured" "grep -q HUB_HOST= /data/victor/hub.env"
chk "no OpenAI key on robot" "! grep -rqs -e OPENAI_API_KEY=. -e sk-proj- /data/victor /etc"
# Image recognition runs on the hub; the robot only has to stream VCT1 VIDEO.
warn() { if eval "$2" >/dev/null 2>&1; then echo "PASS: $1"; else echo "WARN: $1"; fi; }
warn "camera node /dev/video0 present" "test -e /dev/video0"
warn "victor-agent grabbed a camera frame (/data/victor/camera.ok)" "test -f /data/victor/camera.ok"
for u in $(systemctl list-unit-files --no-legend 2>/dev/null | awk "/camera/{print \$1}"); do
  chk "$u not masked" "! systemctl is-enabled $u 2>/dev/null | grep -q masked"
done
/usr/bin/victor-agent status 2>/dev/null || true
exit $fail'
rc=$?

# Hub side: is VIDEO arriving for faces + the edge ONNX vote?
HUB_HTTP="${HUB_HTTP:-http://127.0.0.1:8080}"
if command -v curl >/dev/null && st="$(curl -fsS --max-time 3 "${HUB_HTTP}/status" 2>/dev/null)"; then
  python3 - "$st" <<'PY' || true
import json, sys
s = json.loads(sys.argv[1])
v = s.get("video", 0)
print(("PASS" if v else "WARN") + f": hub received {v} VIDEO frames; edge_vote={s.get('edge_vote')} face={s.get('face')}")
PY
else
  echo "WARN: hub ${HUB_HTTP}/status not reachable (start it: make hub)"
fi
exit $rc
