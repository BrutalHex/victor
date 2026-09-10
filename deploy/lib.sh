#!/usr/bin/env bash
# Shared deploy helpers. Sourced, not executed.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

ROBOT_SSH_IP="${ROBOT_SSH_IP:-192.168.0.6}"
ROBOT_SSH_USER="${ROBOT_SSH_USER:-root}"
ROBOT_SSH_KEY="${ROBOT_SSH_KEY:-${ROOT}/keys/ssh_root_key}"
if [[ "${ROBOT_SSH_KEY}" != /* ]]; then
  ROBOT_SSH_KEY="${ROOT}/${ROBOT_SSH_KEY}"
fi

SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${HOME}/.ssh/victor-cm-%C}"
SSH_OPTS=(
  -i "${ROBOT_SSH_KEY}"
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o HostKeyAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
)
SSH_MASTER_OPTS=(
  "${SSH_OPTS[@]}"
  -o ControlMaster=auto
  -o ControlPath="${SSH_CONTROL_PATH}"
  -o ControlPersist=120
)

robot_ssh() {
  ssh "${SSH_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" "$@"
}

robot_scp() {
  scp -O "${SSH_OPTS[@]}" "$@"
}
