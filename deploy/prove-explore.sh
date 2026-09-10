#!/usr/bin/env bash
# Test wander gates. Does NOT drive while on the charger.
# Off-charger creep only with --drive and a floor/blocks confirmation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

DRIVE=0
if [[ "${1:-}" == "--drive" ]]; then
  DRIVE=1
fi

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

mkdir -p "$(dirname "${SSH_CONTROL_PATH}")"
ssh "${SSH_MASTER_OPTS[@]}" -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || fail "controlmaster"
robot_ssh() { ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" "$@"; }

cleanup() {
  robot_ssh 'rm -f /data/victor/explore.enabled /data/victor/force-cliffs /data/victor/force-skill
    echo idle > /tmp/x; true' 2>/dev/null || true
  ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 'rm -f /data/victor/explore.enabled' 2>/dev/null || true
  ssh "${SSH_OPTS[@]}" -O exit "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
}
trap cleanup EXIT

echo "== SSH =="
robot_ssh true || fail "ssh"
pass "ssh"

echo "== baseline: no explore.enabled =="
robot_ssh 'rm -f /data/victor/explore.enabled'
sleep 0.4
MOT=$(robot_ssh 'tr -d "\r\n" < /data/victor/motors.txt')
echo "motors $MOT"
echo "$MOT" | grep -q '^0,0,' || fail "wheels not zero without flag"
pass "no flag → wheels 0"

TELEM=$(robot_ssh 'tail -n 1 /data/victor/telemetry.log')
echo "telem $TELEM"
ON_CHARGER=0
if echo "$TELEM" | grep -q 'charger=[4-9][0-9][0-9][0-9]'; then
  ON_CHARGER=1
fi
# flags bit2 = on_charger
if echo "$TELEM" | grep -Eq 'flags=([1-9]*[4567]|1[2-9]|[2-9][0-9])'; then
  :
fi
if echo "$TELEM" | grep -qE 'flags=(4|5|6|7|12|13|14|15)( |$)'; then
  ON_CHARGER=1
fi
echo "on_charger_guess=$ON_CHARGER"

echo "== explore.enabled while on charger must still hold wheels =="
robot_ssh 'echo 1 > /data/victor/explore.enabled'
# ask for creep anyway
robot_ssh 'echo creep_forward > /data/victor/force-skill' 2>/dev/null || true
sleep 0.8
MOT=$(robot_ssh 'tr -d "\r\n" < /data/victor/motors.txt')
VETO=$(robot_ssh 'tr -d "\r\n" < /data/victor/veto.txt')
echo "motors $MOT veto $VETO"
echo "$MOT" | grep -q '^0,0,' || fail "on charger / gated wander leaked wheel pwm: $MOT"
pass "explore.enabled + charger/veto gate → wheels 0"

if [[ "$DRIVE" != 1 ]]; then
  echo
  echo "Stopped before off-charger creep. Robot must be on the floor/blocks, not a desk edge."
  echo "Then: ./deploy/prove-explore.sh --drive"
  exit 0
fi

if [[ "$ON_CHARGER" == 1 ]]; then
  fail "--drive refused: telemetry still shows charger present. Lift Vector off the contacts onto the floor/blocks first."
fi

echo "== off-charger creep ~1.5s then stop =="
robot_ssh 'echo 1 > /data/victor/explore.enabled'
sleep 1.2
MOT=$(robot_ssh 'tr -d "\r\n" < /data/victor/motors.txt')
echo "motors during wander $MOT"
robot_ssh 'rm -f /data/victor/explore.enabled'
sleep 0.6
MOT2=$(robot_ssh 'tr -d "\r\n" < /data/victor/motors.txt')
echo "motors after flag removed $MOT2"
echo "$MOT2" | grep -q '^0,0,' || fail "wheels did not stop after removing explore.enabled"
pass "flag removed → wheels 0"
echo "wander pwm while enabled: $MOT"
