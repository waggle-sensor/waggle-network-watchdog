#!/bin/bash -e

docker build -f Dockerfile.test -t waggle-network-watchdog .
docker run --rm waggle-network-watchdog /bin/sh -c '
getvalue() {
    if test -e "$1"; then
        cat "$1"
    else
        echo "$2"
    fi
}

loops=20
for i in $(seq $loops); do
    echo STATE current_media $(getvalue /tmp/current-slot 0)
    echo STATE mmc_network_reset_count $(getvalue /etc/waggle/nw/network_reset_count 0)
    echo STATE mmc_soft_reset_count $(getvalue /etc/waggle/nw/soft_reset_count 0)
    echo STATE mmc_hard_reset_count $(getvalue /etc/waggle/nw/hard_reset_count 0)
    echo STATE sd_network_reset_count $(getvalue /media/scratch/etc/waggle/nw/network_reset_count 0)
    echo STATE sd_soft_reset_count $(getvalue /media/scratch/etc/waggle/nw/soft_reset_count 0)
    echo STATE sd_hard_reset_count $(getvalue /media/scratch/etc/waggle/nw/hard_reset_count 0)
    /usr/bin/test_waggle_network_watchdog.py -l $i -n $loops
done
'
