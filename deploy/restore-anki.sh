#!/usr/bin/env bash
# Give the body back to stock vic-engine. SSH is left alone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

robot_ssh 'systemctl stop victor-agent.service || true
rm -f /data/victor/anki.masked /data/victor/spine.ok
systemctl start anki-robot.target
sleep 2
systemctl start victor-agent.service || true
systemctl is-active vic-engine.service
systemctl is-active sshd.socket
'
echo "anki restored on ${ROBOT_SSH_IP}"
