SUMMARY = "Victor running image: vendor kernel + our rootfs + victor-agent"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit core-image
require victor-image.inc

# Writes the inactive A/B slot only. Recovery remains if this image fails to boot.
DESCRIPTION = "Do not dd Ubuntu over system_a. Install via recovery ota-start HTTP."
