#!/usr/bin/env bash
# Cross-compile victor-agent for APQ8009 and install it on a running robot.
#
# On our OTA image (/etc/victor-release present) the image unit stays as it
# is: the new binary goes to /data/victor/victor-agent and a systemd drop-in
# (victor-agent.service.d/10-sync.conf) points ExecStart at it, so the image's
# /usr/bin/victor-agent remains the fallback and the next OTA (which rewrites
# the system slot) drops the override. Remove the drop-in to go back.
# On a stock/WireOS robot the legacy unit from robot/systemd is installed.
# Nothing here touches sshd, ssh.enabled or authorized_keys.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

[[ "${SKIP_BUILD:-0}" == 1 ]] || make -C "$ROOT" agent-arm
BIN="${ROOT}/robot/agent/dist/victor-agent"
DEST="${ROBOT_SSH_USER}@${ROBOT_SSH_IP}"

robot_scp "$BIN" "${DEST}:/data/victor/victor-agent.new"

if robot_ssh 'test -f /etc/victor-release'; then
  robot_ssh 'set -e
chmod +x /data/victor/victor-agent.new
mv -f /data/victor/victor-agent.new /data/victor/victor-agent
D=/etc/systemd/system/victor-agent.service.d
want="[Service]
ExecStart=
ExecStart=/data/victor/victor-agent run"
if [ "$(cat $D/10-sync.conf 2>/dev/null)" != "$want" ]; then
  mount -o remount,rw / || true
  mkdir -p $D
  printf "%s\n" "$want" > $D/10-sync.conf
  sync
  mount -o remount,ro / || true
fi
systemctl daemon-reload
systemctl restart victor-agent.service
sleep 2
systemctl is-active victor-agent.service
systemctl show victor-agent.service -p ExecStart | cut -c1-120'
else
  robot_scp "${ROOT}/robot/systemd/victor-agent.service" "${DEST}:/tmp/victor-agent.service"
  robot_ssh 'mkdir -p /data/victor /run/victor; mount -o remount,rw / || true
chmod +x /data/victor/victor-agent.new
systemctl stop victor-agent.service 2>/dev/null || true
mv -f /data/victor/victor-agent.new /data/victor/victor-agent
mkdir -p /etc/systemd/system
cp /tmp/victor-agent.service /etc/systemd/system/victor-agent.service
systemctl daemon-reload
systemctl enable victor-agent.service
systemctl restart victor-agent.service
systemctl is-active victor-agent.service
mount -o remount,ro / || true
'
fi
echo "agent installed on ${ROBOT_SSH_IP}"
