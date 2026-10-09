#!/bin/sh
# Running image does not advertise BLE. Recoveryfs may keep BLE for unbrick.
# The OTA packer also masks these units in the sysfs at build time (rootfs is
# read-only on the robot), so this is the runtime belt to that brace.
mkdir -p /data/victor
if [ ! -f /data/victor/ble.disabled ]; then
  echo 1 > /data/victor/ble.disabled
fi
for u in ankibluetoothd.service btproperty.service vic-switchboard.service bluetooth.service; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl mask "$u" 2>/dev/null || systemctl mask --runtime "$u" 2>/dev/null || true
done
rfkill block bluetooth 2>/dev/null || true
exit 0
