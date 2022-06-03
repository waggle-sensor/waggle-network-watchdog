#!/bin/bash -e

docker run --rm \
  -e NAME="waggle-network-watchdog" \
  -e DESCRIPTION="NX Network Watchdog Services" \
  -v "$PWD:/repo" \
  waggle/waggle-deb-builder:latest
