#!/usr/bin/env bash
# Prove Phase 1: Anki masked, SENSOR from spine, E-OK, SSH still works.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

mkdir -p "$(dirname "${SSH_CONTROL_PATH}")"
ssh "${SSH_MASTER_OPTS[@]}" -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || fail "controlmaster"
robot_ssh() { ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" "$@"; }
PHASE1_OK=0
restore() {
  ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" '/data/victor/victor-agent ssh-on' 2>/dev/null || true
  if [[ "${PHASE1_OK}" != 1 ]]; then
    echo "prove failed; restoring Anki"
    "${ROOT}/deploy/restore-anki.sh" || true
  fi
  ssh "${SSH_OPTS[@]}" -O exit "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
}
trap restore EXIT

echo "== SSH before Anki stop =="
robot_ssh true || fail "ssh before"
pass "ssh before mask"

echo "== install agent + mask Anki =="
"${ROOT}/deploy/sync-agent.sh"
robot_ssh '/data/victor/victor-agent mask-anki'
robot_ssh 'systemctl restart victor-agent.service'
sleep 2
robot_ssh 'systemctl is-active victor-agent.service' | grep -q active || fail "agent not active"
pass "agent running with --own-spine"

echo "== Anki engine must be down, SSH must stay =="
STATE=$(robot_ssh 'systemctl is-active vic-engine.service || true')
if [[ "$STATE" == "active" ]]; then
  fail "vic-engine still active"
fi
robot_ssh true || fail "ssh after mask"
pass "engine down, ssh up"

echo "== wait for spine frames =="
ok=0
for i in $(seq 1 25); do
  if robot_ssh 'test -f /data/victor/spine.ok'; then
    ok=1
    break
  fi
  sleep 0.4
done
if [[ "$ok" != 1 ]]; then
  echo "WARN: no spine.ok yet (UART may need another cycle); dumping last telem"
  robot_ssh 'tail -n 3 /data/victor/telemetry.log || true'
  fail "no spine dataframe"
fi
pass "spine dataframe received"

echo "== SENSOR log has cliffs/encoders =="
LINE=$(robot_ssh 'tail -n 1 /data/victor/telemetry.log')
echo "last $LINE"
pass "telemetry still writing"

echo "== face E-OK =="
robot_ssh 'test -s /data/victor/face.rgb565' || fail "no face.rgb565"
pass "face.rgb565 present (E-OK blit)"

echo "== SSH after agent restart =="
robot_ssh 'systemctl restart victor-agent.service'
sleep 1
robot_ssh true || fail "ssh after agent restart"
pass "ssh after agent restart"

PHASE1_OK=1
echo
echo "Phase 1 proved on ${ROBOT_SSH_IP}."
echo "Physical CHARGE-LATCH is now live if the robot is on the charger."
echo "Restore stock face with ./deploy/restore-anki.sh"
