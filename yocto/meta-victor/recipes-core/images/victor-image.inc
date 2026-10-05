# Yocto first boot: sshd on, BLE units masked.
# Vendor kernel + modules + firmware stay on the APQ8009; this layer is rootfs + agent.
IMAGE_FEATURES += "ssh-server-dropbear"
DISTRO_FEATURES:append = " systemd wifi"
VIRTUAL-RUNTIME_init_manager = "systemd"

IMAGE_INSTALL:append = " \
    victor-agent \
    victor-ble-mask \
    victor-set-hub-ip \
    victor-firstboot \
    dropbear \
    openssh \
"

# /data must be rw,exec. Do not freeze /etc/hosts read-only.
# recoveryfs is a separate image (victor-image-recovery) and is never the
# A/B slot written by ota-start. Keep SBL + ABOOT + recoveryfs.

IMAGE_FSTYPES = "ext4 tar.gz"
