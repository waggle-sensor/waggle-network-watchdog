#!/bin/bash -e

docker run --rm \
  -e NAME="waggle-network-watchdog" \
  -e DESCRIPTION="NX Network Watchdog Services" \
  -e "MAINTAINER=sagecontinuum.org" \
  -v "$PWD:/repo" \
  waggle/waggle-deb-builder:0.2.0
