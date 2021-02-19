#!/bin/bash -e

mkdir -p /tmp/reg

# Build the nework-watchdog debian package
BASEDIR=/tmp/reg
NAME=waggle-network-watchdog
ARCH=all

mkdir -p ${BASEDIR}/DEBIAN
cat > ${BASEDIR}/DEBIAN/control <<EOL
Package: ${NAME}
Version: ${VERSION_LONG}
Maintainer: sagecontinuum.org
Description: NX Network Watchdog Services
Architecture: ${ARCH}
Priority: optional
EOL

cp -p deb/install/postinst ${BASEDIR}/DEBIAN/
cp -p deb/install/prerm ${BASEDIR}/DEBIAN/

cp -pr ROOTFS/* ${BASEDIR}/

dpkg-deb --root-owner-group --build ${BASEDIR} "${NAME}_${VERSION_SHORT}_${ARCH}.deb"
mv *.deb /output/
