# /data is rw,exec. Stock Vector often mounts /data noexec.
do_install:append() {
    install -d ${D}/data/victor
    if [ -f ${D}${sysconfdir}/fstab ]; then
        grep -q '/data' ${D}${sysconfdir}/fstab || \
            echo '/dev/data  /data  ext4  rw,exec,noatime  0  0' >> ${D}${sysconfdir}/fstab
    fi
}

FILES:${PN} += "/data /data/victor"
