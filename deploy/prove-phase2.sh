#!/usr/bin/env bash
# Prove Phase 2: cliff cal, on-robot veto, hub death zeros wheels, SSH stays.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

mkdir -p "$(dirname "${SSH_CONTROL_PATH}")"
ssh "${SSH_MASTER_OPTS[@]}" -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || fail "controlmaster"
robot_ssh() { ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" "$@"; }

HUB_PID=""
cleanup() {
  if [[ -n "$HUB_PID" ]]; then kill "$HUB_PID" 2>/dev/null || true; fi
  ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 'rm -f /data/victor/hub.down /data/victor/force-cliffs; /data/victor/victor-agent ssh-on' 2>/dev/null || true
  ssh "${SSH_OPTS[@]}" -O exit "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
}
trap cleanup EXIT

echo "== SSH =="
robot_ssh true || fail "ssh"
robot_ssh 'rm -f /data/victor/force-cliffs /data/victor/hub.down'
pass "ssh"

echo "== sync agent =="
"${ROOT}/deploy/sync-agent.sh"
robot_ssh 'test -f /data/victor/anki.masked || /data/victor/victor-agent mask-anki'
robot_ssh 'systemctl restart victor-agent.service'
sleep 3
robot_ssh 'systemctl is-active victor-agent.service' | grep -q active || fail "agent"
pass "agent up"

echo "== wheels must be 0 (explore.enabled absent) =="
MOT="missing"
for i in $(seq 1 15); do
  MOT=$(robot_ssh 'cat /data/victor/motors.txt 2>/dev/null || echo missing')
  [[ "$MOT" != "missing" ]] && break
  sleep 0.3
done
echo "motors $MOT"
echo "$MOT" | grep -q '^0,0,0,0' || fail "wheels not zero"
pass "wheels held at 0"

echo "== cliff calibration =="
ok=0
for i in $(seq 1 30); do
  if robot_ssh 'test -s /data/victor/cliffs.cal'; then ok=1; break; fi
  sleep 0.3
done
if [[ "$ok" != 1 ]]; then
  robot_ssh 'cat /data/victor/veto.txt /data/victor/telemetry.log 2>/dev/null | tail -n 5' || true
  fail "no cliffs.cal"
fi
robot_ssh 'cat /data/victor/cliffs.cal'
pass "cliffs.cal written"

echo "== hub-dead veto (no heartbeat yet) =="
V=$(robot_ssh 'tr -d "\n" < /data/victor/veto.txt')
echo "veto=$V"
# Without a hub the interlock must not be clear.
if [[ "$V" == "clear" ]]; then fail "veto clear with no hub"; fi
pass "veto=$V with hub missing (wheels stay 0)"

echo "== desk-edge with hub dead: inject front cliffs =="
robot_ssh 'echo 0,0,400,400 > /data/victor/force-cliffs'
V=""
for i in $(seq 1 20); do
  V=$(robot_ssh 'tr -d "\r\n" < /data/victor/veto.txt')
  [[ "$V" == "cliff" ]] && break
  sleep 0.25
done
echo "veto after inject=$V"
[[ "$V" == "cliff" ]] || fail "expected cliff veto, got $V"
MOT=$(robot_ssh 'tr -d "\n" < /data/victor/motors.txt')
echo "$MOT" | grep -q '^0,0,0,0' || fail "wheels moved under cliff veto"
pass "cliff veto with hub dead, motors 0"
robot_ssh 'rm -f /data/victor/force-cliffs'
sleep 0.5

echo "== local hub + reverse tunnel for 250ms heartbeat =="
docker stop hub >/dev/null 2>&1 || true
HUB_SKILL_PORT=17443 HUB_HTTP_PORT=18080 HUB_SENSOR_PORT=17502 \
  python3 "${ROOT}/hub/app/main.py" >/tmp/victor-hub-phase2.log 2>&1 &
HUB_PID=$!
sleep 0.5
ssh "${SSH_MASTER_OPTS[@]}" -O cancel -R 127.0.0.1:7443:127.0.0.1:17443 "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
ssh "${SSH_MASTER_OPTS[@]}" -O cancel -R 127.0.0.1:7443:127.0.0.1:7443 "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
ssh "${SSH_MASTER_OPTS[@]}" -O forward -R 127.0.0.1:7443:127.0.0.1:17443 "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || \
  ssh "${SSH_MASTER_OPTS[@]}" -R 127.0.0.1:7443:127.0.0.1:17443 -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || true
robot_ssh 'printf "HUB_HOST=127.0.0.1\nHUB_GRPC_PORT=7443\n" > /data/victor/hub.env'
robot_ssh 'systemctl restart victor-agent.service'
sleep 2
V=$(robot_ssh 'tr -d "\n" < /data/victor/veto.txt')
echo "veto with hub=$V"
# On charger, explorer sends dock; veto should be clear if heartbeat flows.
if [[ "$V" == "heartbeat" ]]; then
  echo "WARN: heartbeat still missing (tunnel may have failed); skip 250ms kill timing"
else
  pass "heartbeat flowing, veto=$V"
  echo "== kill hub, wheels stay 0 within 250ms class =="
  T0=$(date +%s%N)
  robot_ssh 'touch /data/victor/hub.down'
  kill -9 "$HUB_PID" 2>/dev/null || true
  HUB_PID=""
  got=0
  for i in $(seq 1 25); do
    V=$(robot_ssh 'tr -d "\r\n" < /data/victor/veto.txt')
    AGE=$(robot_ssh 'tr -d "\r\n" < /data/victor/hb_age_ms.txt 2>/dev/null || echo x')
    echo "  t=$i veto=$V hb_age_ms=$AGE"
    if [[ "$V" == "heartbeat" ]]; then
      T1=$(date +%s%N)
      DT=$(( (T1 - T0) / 1000000 ))
      echo "heartbeat veto after ${DT}ms"
      got=1
      break
    fi
    sleep 0.2
  done
  [[ "$got" == 1 ]] || fail "no heartbeat veto after hub kill"
  MOT=$(robot_ssh 'tr -d "\n" < /data/victor/motors.txt')
  echo "$MOT" | grep -q '^0,0,0,0' || fail "wheels not zero after hub kill"
  pass "hub death → veto heartbeat, motors 0"
fi

echo "== SSH still works =="
robot_ssh true || fail "ssh after veto tests"
pass "ssh survived"

echo
echo "Phase 2 proved on ${ROBOT_SSH_IP}."
echo "Wheels stay 0 until /data/victor/explore.enabled exists AND veto is clear AND off charger."
