#!/usr/bin/env bash
# Phase 1: stop Anki, give victor-agent the spine, keep SSH.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

echo "SSH before mask:"
robot_ssh true

"${ROOT}/deploy/sync-agent.sh"

robot_ssh '/data/victor/victor-agent mask-anki'
# agent restarts via systemd ExecStart --own-spine
robot_ssh 'systemctl restart victor-agent.service; systemctl is-active victor-agent.service'

echo "SSH after Anki stop:"
robot_ssh true
echo "engine=$(robot_ssh 'systemctl is-active vic-engine.service || true')"
echo "done. restore with deploy/restore-anki.sh"
