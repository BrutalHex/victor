SUMMARY = "victor-agent: CHARGE-LATCH, telemetry, SSH latch"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://victor-agent.service"

inherit systemd

SYSTEMD_SERVICE:${PN} = "victor-agent.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/victor-agent.service ${D}${systemd_system_unitdir}/
    install -d ${D}/data/victor
}

FILES:${PN} += "${systemd_system_unitdir}/victor-agent.service /data/victor"
