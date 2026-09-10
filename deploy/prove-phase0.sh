#!/usr/bin/env bash
# Prove Phase 0 against the live robot: SSH, CHARGE-LATCH, BLE mask, telemetry,
# no OpenAI key.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
restore_ssh() {
  ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" '/data/victor/victor-agent ssh-on' 2>/dev/null || true
  ssh "${SSH_OPTS[@]}" -O exit "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
}
trap restore_ssh EXIT

echo "== SSH probe ${ROBOT_SSH_IP}:22 =="
timeout 3 bash -c "echo >/dev/tcp/${ROBOT_SSH_IP}/22" || fail "port 22 closed"
robot_ssh true || fail "ssh root@${ROBOT_SSH_IP}"
pass "ssh root@${ROBOT_SSH_IP} true"

echo "== OpenAI key must not be on the robot =="
robot_ssh 'if [ -n "$OPENAI_API_KEY" ]; then echo OPENAI_API_KEY_in_env; exit 1; fi
for f in /data/victor/.env /data/openai.key /anki/etc/openai; do
  if [ -e "$f" ]; then echo "key file $f"; exit 1; fi
done
true' || fail "possible OpenAI key on robot"
pass "no OpenAI key on robot filesystem"

echo "== install / restart agent =="
"${ROOT}/deploy/sync-agent.sh"

echo "== telemetry while SSH is on =="
sleep 1
robot_ssh 'test -s /data/victor/telemetry.log' || fail "no telemetry log"
LINES=$(robot_ssh 'wc -l < /data/victor/telemetry.log')
pass "telemetry log lines=${LINES}"

echo "== CHARGE-LATCH simulate toggles port 22 =="
# Hold a muxed SSH session so we can still talk after sshd.socket stops.
mkdir -p "$(dirname "${SSH_CONTROL_PATH}")"
ssh "${SSH_MASTER_OPTS[@]}" -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || fail "ssh controlmaster"
robot_ssh() { ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" "$@"; }
robot_ssh '/data/victor/victor-agent latch-simulate'
sleep 1
if timeout 2 bash -c "echo >/dev/tcp/${ROBOT_SSH_IP}/22" 2>/dev/null; then
  fail "port 22 still open after CHARGE-LATCH off"
fi
pass "port 22 closed for new connections"

echo "== existing admin path + telemetry survive SSH off =="
robot_ssh 'test -s /data/victor/telemetry.log && pgrep -f /data/victor/victor-agent >/dev/null' \
  || fail "agent died when SSH listener stopped"
AFTER=$(robot_ssh 'wc -l < /data/victor/telemetry.log')
pass "agent alive, telemetry lines=${AFTER}"

echo "== CHARGE-LATCH on again =="
robot_ssh '/data/victor/victor-agent latch-simulate'
sleep 1
timeout 3 bash -c "echo >/dev/tcp/${ROBOT_SSH_IP}/22" || fail "port 22 did not reopen"
robot_ssh true || fail "ssh after re-enable"
pass "port 22 listening again"

echo "== mask BLE =="
"${ROOT}/deploy/mask-ble.sh"
STATE=$(robot_ssh 'systemctl is-active ankibluetoothd.service || true')
if [[ "$STATE" == "active" ]]; then
  fail "ankibluetoothd still active"
fi
robot_ssh 'test -f /data/victor/ble.disabled' || fail "ble.disabled missing"
pass "BLE units stopped, /data/victor/ble.disabled present"

echo "== SSH still works after BLE mask =="
robot_ssh true || fail "ssh after ble mask"
pass "SSH survived BLE mask"

echo
echo "Phase 0 proved on ${ROBOT_SSH_IP} (Vector $(robot_ssh hostname))."
echo "Physical CHARGE-LATCH (double-click, lift, triple-click on charger) uses the same FSM."
echo "first-flash OTA is implemented in deploy/first-flash; not run (no our .ota yet, Phase 4)."
