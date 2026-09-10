#!/usr/bin/env bash
# Cross-compile victor-agent for APQ8009 and install it on the robot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

make -C "$ROOT" agent-arm
BIN="${ROOT}/robot/agent/dist/victor-agent"

robot_ssh 'mkdir -p /data/victor /run/victor; mount -o remount,rw / || true; systemctl stop victor-agent.service 2>/dev/null || true'
robot_scp "$BIN" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/data/victor/victor-agent"
robot_scp "${ROOT}/robot/systemd/victor-agent.service" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/tmp/victor-agent.service"
robot_ssh 'chmod +x /data/victor/victor-agent
mkdir -p /etc/systemd/system
cp /tmp/victor-agent.service /etc/systemd/system/victor-agent.service
systemctl daemon-reload
systemctl enable victor-agent.service
systemctl restart victor-agent.service
systemctl is-active victor-agent.service
mount -o remount,ro / || true
'
echo "agent installed on ${ROBOT_SSH_IP}"
