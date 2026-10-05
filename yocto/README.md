# Yocto (`meta-victor`)

Vendor kernel + our rootfs + `victor-agent`. Do not flash Ubuntu onto the APQ8009.

## Slots

| Partition | Role |
|---|---|
| SBL + ABOOT | Keep. Never replace with a generic bootloader. |
| recoveryfs | Keep. BLE allowed here so unbrick / first OTA still works. |
| system_a / system_b | Our running image. `ota-start` writes the **inactive** slot, marks bootable, reboots. |

## First boot of our OTA

- SSH ON (`/data/victor/ssh.enabled=1`) so `ble-bootstrap` can finish
- BLE units masked (`victor-ble-mask`)
- `/data` mounted `rw,exec`

## Build (on the development machine, Docker already installed)

Point `kas` / `bitbake` at this layer plus the vendor BSP. Then:

```
./deploy/make-ota.sh --boot boot.img --sysfs sysfs.img --out dist/victor.ota
```

Recovery pulls **HTTP**, not HTTPS:

```
./deploy/first-flash --pin … --ssid … --password … --url http://<dev-ip>:8088/victor.ota
```
