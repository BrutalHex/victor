#!/usr/bin/env bash
# Rewrite the managed /etc/hosts block on the robot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

HUB_IP="${1:-${HUB_IP:-}}"
if [[ -z "$HUB_IP" ]]; then
  HUB_IP="$(hostname -I | awk '{print $1}')"
fi
NAME="${HUB_PUBLIC_NAME:-robot.mohammadabbasi.com}"

robot_scp "${ROOT}/robot/scripts/victor-set-hub-ip" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/tmp/victor-set-hub-ip"
robot_ssh "chmod +x /tmp/victor-set-hub-ip; mount -o remount,rw / || true
mkdir -p /data/victor /usr/bin
cp /tmp/victor-set-hub-ip /data/victor/victor-set-hub-ip
/data/victor/victor-set-hub-ip ${HUB_IP} ${NAME}
echo HUB_IP=${HUB_IP} > /data/victor/hub.env
echo HUB_HOST=${NAME} >> /data/victor/hub.env
mount -o remount,ro / || true
"
echo "hub ${NAME} -> ${HUB_IP}"
