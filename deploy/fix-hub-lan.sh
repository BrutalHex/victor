#!/usr/bin/env bash
# Point Vector at the laptop Wi-Fi IP (192.168.0.202). If Windows Firewall
# still blocks LAN TCP, DNAT that IP:7443 to the SSH reverse tunnel so the
# agent still talks to the WSL hub.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

HUB_IP="${1:-${HUB_IP:-192.168.0.202}}"
NAME="${HUB_PUBLIC_NAME:-robot.mohammadabbasi.com}"

"${ROOT}/deploy/push-hub-ip.sh" "$HUB_IP"

mkdir -p "$(dirname "${SSH_CONTROL_PATH}")"
ssh "${SSH_MASTER_OPTS[@]}" -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || true
ssh "${SSH_MASTER_OPTS[@]}" -O cancel -R 127.0.0.1:7443:127.0.0.1:7443 "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
ssh "${SSH_MASTER_OPTS[@]}" -O forward -R 127.0.0.1:7443:127.0.0.1:7443 "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || \
  ssh "${SSH_MASTER_OPTS[@]}" -R 127.0.0.1:7443:127.0.0.1:7443 -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}"

LAN_OPEN=0
if robot_ssh "echo >/dev/tcp/${HUB_IP}/7443" 2>/dev/null; then
  LAN_OPEN=1
fi

if [[ "$LAN_OPEN" == 1 ]]; then
  robot_ssh 'iptables -t nat -D OUTPUT -p tcp -d '"${HUB_IP}"' --dport 7443 -j REDIRECT --to-ports 7443 2>/dev/null || true'
  echo "LAN ${HUB_IP}:7443 is open — robot talks to the laptop directly"
else
  robot_ssh "iptables -t nat -C OUTPUT -p tcp -d ${HUB_IP} --dport 7443 -j REDIRECT --to-ports 7443 2>/dev/null || \
    iptables -t nat -A OUTPUT -p tcp -d ${HUB_IP} --dport 7443 -j REDIRECT --to-ports 7443"
  echo "Windows Firewall still blocks ${HUB_IP}:7443 from Wi-Fi."
  echo "Redirecting robot->${HUB_IP}:7443 to the SSH tunnel for now."
  echo "To open it for real, Run as administrator: Desktop\\open-hub-ports.bat"
fi

robot_ssh 'systemctl restart victor-agent.service; sleep 1; cat /data/victor/hub.env; echo ---; grep managed-by -A2 /etc/hosts'
sleep 2
echo '==== hub ===='
curl -sf http://127.0.0.1:8080/status | python3 -c 'import sys,json; s=json.load(sys.stdin); print("from",s.get("last_from"),"hz",s.get("hz"),"hb",s.get("heartbeats"),"skill",s.get("skill"))'
