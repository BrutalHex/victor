SUMMARY = "Mask BLE after first boot of our image"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://victor-ble-mask.service file://victor-ble-mask.sh"

inherit systemd

SYSTEMD_SERVICE:${PN} = "victor-ble-mask.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${systemd_system_unitdir} ${D}${bindir}
    install -m 0644 ${WORKDIR}/victor-ble-mask.service ${D}${systemd_system_unitdir}/
    install -m 0755 ${WORKDIR}/victor-ble-mask.sh ${D}${bindir}/victor-ble-mask
}
