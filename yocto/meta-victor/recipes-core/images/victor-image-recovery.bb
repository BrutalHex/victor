SUMMARY = "Victor recoveryfs (keep this slot; BLE allowed here for unbrick only)"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit core-image

IMAGE_FEATURES += "ssh-server-dropbear"
IMAGE_INSTALL:append = " dropbear"
IMAGE_FSTYPES = "ext4"

# Recovery may keep BLE so first-flash / unbrick still works.
# Do not install victor-ble-mask here.
DESCRIPTION = "Kept across OTA. HTTP ota-start from this image writes the inactive A/B slot."
