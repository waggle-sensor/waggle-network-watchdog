#!/bin/bash -e

docker build -f Dockerfile.test -t waggle-network-watchdog .
docker run -it --rm waggle-network-watchdog /bin/sh -c '
getcount() {
    if test -e "$1"; then
        cat "$1"
    else
        echo x
    fi
}

for _ in $(seq 20); do
    echo mmc_network_reset_count $(getcount /etc/waggle/nw/network_reset_count)
    echo mmc_soft_reset_count $(getcount /etc/waggle/nw/soft_reset_count)
    echo mmc_hard_reset_count $(getcount /etc/waggle/nw/hard_reset_count)
    echo sd_network_reset_count $(getcount /media/scratch/etc/waggle/nw/network_reset_count)
    echo sd_soft_reset_count $(getcount /media/scratch/etc/waggle/nw/soft_reset_count)
    echo sd_hard_reset_count $(getcount /media/scratch/etc/waggle/nw/hard_reset_count)

    /usr/bin/test_waggle_network_watchdog.py
done
'
