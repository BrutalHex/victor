#!/bin/sh
# First boot of our OTA: SSH ON so bootstrap can finish, BLE masked, /data rw,exec.
set -e
mkdir -p /data/victor
if [ ! -f /data/victor/ssh.enabled ]; then
  echo 1 > /data/victor/ssh.enabled
fi
if [ ! -f /data/victor/ble.disabled ]; then
  echo 1 > /data/victor/ble.disabled
fi
# /data must be executable for the agent binary when it lives here.
mount -o remount,rw,exec /data 2>/dev/null || true
exit 0
