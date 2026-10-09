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
for u in ankibluetoothd vic-switchboard btproperty; do
  chk "$u masked/inactive" "! systemctl is-active -q $u.service"
done
if [ -e /dev/rfkill ]; then chk "bluetooth rfkill blocked" "! rfkill list bluetooth 2>/dev/null | grep -q \"Soft blocked: no\""; else echo "INFO: no /dev/rfkill on this kernel"; fi
chk "no HCI device up" "! ls /sys/class/bluetooth/hci* >/dev/null 2>&1 || ! grep -q 1 /sys/class/bluetooth/hci*/power 2>/dev/null"
S=$(tr " " "\n" </proc/cmdline | sed -n "s/^androidboot.slot_suffix=_//p")
chk "A/B slot $S marked successful" "/bin/bootctl-anki $S status $S | grep -q \"successful: 1\""
chk "victor-agent active" "systemctl is-active -q victor-agent.service"
chk "vic-anim stopped (agent owns the face)" "! systemctl is-active -q vic-anim.service"
chk "face panel initialised by agent" "grep -q ready=true /data/victor/face.txt"
chk "face backlight on" "[ \$(cat /sys/class/leds/face-backlight-left/brightness) -gt 0 ]"
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
