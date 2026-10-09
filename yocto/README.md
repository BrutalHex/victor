# Yocto (`meta-victor`)

Vendor kernel + our rootfs + `victor-agent`. Do not flash Ubuntu onto the APQ8009.

## Slots

| Partition | Role |
|---|---|
| SBL + ABOOT | Keep. Never replace with a generic bootloader. |
| recoveryfs | Keep. BLE allowed here so unbrick / first OTA still works. |
| system_a / system_b | Our running image. `ota-start` writes the **inactive** slot, marks bootable, reboots. |

## First boot of our OTA

- SSH ON (`/data/victor/ssh.enabled=1`, seeded only if missing so CHARGE-LATCH state persists) so `ble-bootstrap` can finish
- BLE units masked (`victor-ble-mask`)
- `/data` mounted `rw,exec`
- `hub.env` defaults to `HUB_HOST=robot.mohammadabbasi.com`

`deploy/make-ota.sh` installs the same firstboot/ble-mask/agent units from this layer into the sysfs it packs, so the OTA and the bitbake image behave the same. Full steps: [deploy/OTA.md](../deploy/OTA.md).

## Build (on the development machine, Docker already installed)

Point `kas` / `bitbake` at this layer plus the vendor BSP. Then:

```
make ota OTA_ARGS="--boot boot.img --sysfs tmp/deploy/images/<machine>/victor-image-<machine>.ext4"
```

Recovery pulls **HTTP**, not HTTPS:

```
./deploy/first-flash --pin … --ssid … --password … --url http://<dev-ip>:8088/victor.ota
```
