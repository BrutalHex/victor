#!/bin/sh
# Running image does not advertise BLE. Recoveryfs may keep BLE for unbrick.
mkdir -p /data/victor
if [ ! -f /data/victor/ble.disabled ]; then
  echo 1 > /data/victor/ble.disabled
fi
for u in ankibluetoothd.service btproperty.service vic-switchboard.service bluetooth.service; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl mask "$u" 2>/dev/null || true
done
rfkill block bluetooth 2>/dev/null || true
