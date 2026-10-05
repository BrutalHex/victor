SUMMARY = "First boot: SSH ON, BLE masked, /data/victor present"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://victor-firstboot.sh file://victor-firstboot.service"

inherit systemd

SYSTEMD_SERVICE:${PN} = "victor-firstboot.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir} ${D}${systemd_system_unitdir} ${D}/data/victor
    install -m 0755 ${WORKDIR}/victor-firstboot.sh ${D}${bindir}/victor-firstboot
    install -m 0644 ${WORKDIR}/victor-firstboot.service ${D}${systemd_system_unitdir}/
}

FILES:${PN} += "${systemd_system_unitdir}/victor-firstboot.service /data/victor"
