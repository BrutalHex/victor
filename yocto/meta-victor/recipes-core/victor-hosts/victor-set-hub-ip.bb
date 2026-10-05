SUMMARY = "Rewrite the managed /etc/hosts block for robot.mohammadabbasi.com"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://victor-set-hub-ip"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/victor-set-hub-ip ${D}${bindir}/victor-set-hub-ip
}

FILES:${PN} = "${bindir}/victor-set-hub-ip"
