#!/usr/bin/env bash
# Disable BLE on the running image. Recoveryfs may keep BLE for unbrick only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

robot_ssh 'set -e
mount -o remount,rw / || true
mkdir -p /data/victor
echo 1 > /data/victor/ble.disabled
for u in ankibluetoothd.service btproperty.service vic-switchboard.service; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl mask "$u" 2>/dev/null || true
done
rfkill block bluetooth 2>/dev/null || true
mount -o remount,ro / || true
echo "ble masked"
systemctl is-active ankibluetoothd.service 2>/dev/null || echo "ankibluetoothd inactive"
'
