#!/usr/bin/env bash
# SSH to the robot with the Vector dropbear/OpenSSH RSA quirks.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"
exec ssh "${SSH_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" "$@"
