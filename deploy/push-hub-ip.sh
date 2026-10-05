#!/usr/bin/env bash
# Rewrite the managed /etc/hosts block on the robot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

HUB_IP="${1:-${HUB_IP:-}}"
if [[ -z "$HUB_IP" ]]; then
  # WSL NAT (172.x) is not reachable from Vector. Prefer the laptop Wi-Fi IP.
  for cand in $(hostname -I); do
    case "$cand" in
      192.168.*|10.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*)
        # skip WSL virtual switch
        case "$cand" in
          172.27.*) continue ;;
        esac
        HUB_IP="$cand"
        break
        ;;
    esac
  done
fi
HUB_IP="${HUB_IP:-192.168.0.202}"
NAME="${HUB_PUBLIC_NAME:-robot.mohammadabbasi.com}"

robot_scp "${ROOT}/robot/scripts/victor-set-hub-ip" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/tmp/victor-set-hub-ip"
robot_ssh "chmod +x /tmp/victor-set-hub-ip; mount -o remount,rw / || true
mkdir -p /data/victor /usr/bin
cp /tmp/victor-set-hub-ip /data/victor/victor-set-hub-ip
cp /tmp/victor-set-hub-ip /usr/bin/victor-set-hub-ip 2>/dev/null || true
/data/victor/victor-set-hub-ip ${HUB_IP} ${NAME}
printf 'HUB_IP=${HUB_IP}\nHUB_HOST=${NAME}\nHUB_GRPC_PORT=7443\n' > /data/victor/hub.env
systemctl restart victor-agent.service 2>/dev/null || true
mount -o remount,ro / || true
"
echo "hub ${NAME} -> ${HUB_IP}"
