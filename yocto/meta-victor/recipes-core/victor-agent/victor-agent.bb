SUMMARY = "victor-agent: CHARGE-LATCH, telemetry, SSH latch, camera, voice"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Cross-compile with: make agent-arm
# Place the binary at files/victor-agent before bitbake, or set VICTOR_AGENT_BIN.
SRC_URI = "file://victor-agent.service"
SRC_URI += "${@'file://victor-agent' if os.path.exists(d.getVar('FILE_DIRNAME') + '/files/victor-agent') else ''}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "victor-agent.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir} ${D}${systemd_system_unitdir} ${D}/data/victor
    install -m 0644 ${WORKDIR}/victor-agent.service ${D}${systemd_system_unitdir}/
    if [ -f ${WORKDIR}/victor-agent ]; then
        install -m 0755 ${WORKDIR}/victor-agent ${D}${bindir}/victor-agent
        install -m 0755 ${WORKDIR}/victor-agent ${D}/data/victor/victor-agent
    fi
}

FILES:${PN} += "${systemd_system_unitdir}/victor-agent.service /data/victor ${bindir}/victor-agent"
