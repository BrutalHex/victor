#!/usr/bin/env bash
# After first-flash: check the robot booted OUR image with the GROK_INSTRUCTIONS
# first-boot state. Read-only except it prints; run from the build machine.
set -euo pipefail
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
chk "bluetooth rfkill blocked" "! rfkill list bluetooth 2>/dev/null | grep -q \"Soft blocked: no\""
chk "victor-agent active" "systemctl is-active -q victor-agent.service"
chk "/data mounted exec" "! grep -E \" /data \" /proc/mounts | grep -q noexec"
chk "hub hostname configured" "grep -q HUB_HOST= /data/victor/hub.env"
chk "no OpenAI key on robot" "! grep -rqs -e OPENAI_API_KEY=. -e sk-proj- /data/victor /etc"
/usr/bin/victor-agent status 2>/dev/null || true
exit $fail'
