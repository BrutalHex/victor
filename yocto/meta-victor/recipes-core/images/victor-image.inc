# Yocto first boot: sshd on, BLE units masked.
IMAGE_FEATURES += "ssh-server-dropbear"
DISTRO_FEATURES:append = " systemd wifi"
VIRTUAL-RUNTIME_init_manager = "systemd"

IMAGE_INSTALL:append = " victor-agent victor-ble-mask openssh"

# /data must be rw,exec. Do not freeze /etc/hosts read-only.
