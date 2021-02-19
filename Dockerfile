FROM ubuntu:18.04

RUN apt-get update -y && apt-get install -y 

RUN mkdir -p /deb/ /ROOTFS/
ADD deb /deb/
ADD ROOTFS /ROOTFS/

COPY release.sh .
