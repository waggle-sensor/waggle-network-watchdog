# Waggle Network Watchdog

Service to monitor the reverse tunnel network connection to Beekeeper and perform network recovery mechanisms in the event that connection test fails.

## Configuration

The configuration of how often the network watchdog runs and recovery recipe is defined by the `/etc/waggle/nw/config.ini` file.

The service also references the `beekeeper` `host` and `port` from the main configuration file: `/etc/waggle/config.ini`

## Recovery Recipe

The specific recovery recipe to follow is defined within the `/etc/waggle/nw/config.ini` file.  But in general there are 3 different steps taken attempting to recovery the reverse tunnel:

1) network service restarts: `systemctl` restarts of core network services
2) system soft reboots: `systemctl reboot` operation
3) system hard reboots: `systemctl poweroff` operation

> Note: the hard-reboot operation relies on an automated service or hardware unit (i.e. [`wagman`](https://github.com/waggle-sensor/wagman)) to power up the host after a small delay of the power off. For Waggle this is done in collaboration with the [`waggle-wagman-watchdog`](https://github.com/waggle-sensor/waggle-wagman-watchdog) service.