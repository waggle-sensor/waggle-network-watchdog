#!/usr/bin/env python3
from pathlib import Path
import subprocess
import time
import logging
import socket
from glob import glob
import configparser
from typing import NamedTuple, Callable
import json


class Action(NamedTuple):
    thresh: int
    func: Callable


class Watchdog:

    def __init__(self, time_func, recovery_actions, health_check, health_check_passed, health_check_failed):
        self.time_func = time_func
        self.recovery_actions = [Action(thresh, func) for thresh, func in recovery_actions]
        # important: tick function expects recovery actions to sorted by thresh in increasing order
        self.recovery_actions.sort(key=lambda a: a.thresh)
        self.called_actions = set()
        self.health_check = health_check
        self.health_check_passed = health_check_passed
        self.health_check_failed = health_check_failed

        self.last_connection_time = self.time_func()

    def update(self):
        health_check_ok = self.health_check()
        health_check_finish_time = self.time_func()
        elapsed = health_check_finish_time - self.last_connection_time

        if health_check_ok:
            self.health_check_passed(elapsed)
            self.last_connection_time = health_check_finish_time
            self.called_actions.clear()
            return

        self.health_check_failed(elapsed)

        # dispatch all activated recovery actions
        for action in self.recovery_actions:
            if elapsed < action.thresh:
                break
            if action in self.called_actions:
                continue
            self.called_actions.add(action)
            logging.debug("calling action %s", action)
            action.func()


class WatchdogConfig(NamedTuple):
    nwwd_ok_file: str

class NetworkWatchdogConfig(NamedTuple):
    check_seconds: float
    check_successive_passes: int
    check_successive_seconds: float
    current_media: int
    network_resets: list
    network_num_resets: int
    network_reset_file: str
    soft_resets: list
    soft_num_resets: int
    soft_reset_file: str
    hard_resets: list
    hard_num_resets: int
    hard_reset_file: str

class ReverseTunnelConfig(NamedTuple):
    beekeeper_server: str
    beekeeper_port: str


def read_config_section_dict(filename, section):
    config = configparser.ConfigParser()

    if not config.read(filename):
        logging.warning(f"could not read config file {filename}")
        return {}

    try:
        return dict(config[section])
    except Exception:
        logging.warning("could not read config section [%s]", section)

    return {}

def read_watchdog_config(filename, section="watchdog"):
    d = read_config_section_dict(filename, section)

    return WatchdogConfig(
        nwwd_ok_file=d.get("ssh_ok_file", None),
    )

def read_network_watchdog_config(filename):
    all_settings = read_config_section_dict(filename, "all")
    network_reset_settings = read_config_section_dict(filename, "network-reboot")
    soft_reset_settings = read_config_section_dict(filename, "soft-reboot")
    hard_reset_settings = read_config_section_dict(filename, "hard-reboot")

    sd_card_storage_loc = ''
    if read_current_media():
        sd_card_storage_loc = all_settings.get("sd_card_storage_loc", None)
    
    return NetworkWatchdogConfig(
        current_media=read_current_media(),
        network_resets=json.loads(network_reset_settings.get("resets", None)),
        network_num_resets=int(network_reset_settings.get("num_resets", 0)),
        network_reset_file=sd_card_storage_loc+network_reset_settings.get("current_reset_file", None),
        
	soft_resets=json.loads(soft_reset_settings.get("resets", None)),
        soft_num_resets=int(soft_reset_settings.get("num_resets", 0)),
        soft_reset_file=sd_card_storage_loc+soft_reset_settings.get("current_reset_file", None),
	
	hard_resets=json.loads(hard_reset_settings.get("resets", None)),
        hard_num_resets=int(hard_reset_settings.get("num_resets", 0)),
        hard_reset_file=sd_card_storage_loc+hard_reset_settings.get("current_reset_file", None),
        
	check_seconds=float(all_settings.get("check_seconds", 15.0)),
        check_successive_passes=int(all_settings.get("check_successive_passes", 3)),
        check_successive_seconds=float(all_settings.get("check_successive_seconds", 5.0)),
    )

def read_reverse_tunnel_config(filename, section="reverse-tunnel"):
    d = read_config_section_dict(filename, section)

    return ReverseTunnelConfig(
        beekeeper_server=d.get("host", None),
        beekeeper_port=d.get("port", None),
    )


def update_systemd_watchdog():
    try:
        subprocess.check_call(["systemd-notify", "WATCHDOG=1"])
    except Exception:
        logging.warning("skipping reset of systemd watchdog")


def ssh_connection_ok(server, port):
    try:

        # do a lookup of the ip for the server
        server_addr = f"{socket.gethostbyname(server)}:{port}"
        logging.debug(f"checking for ssh connection to [{server_addr}]")

        return (
            server_addr
            in subprocess.check_output(["ss", "-t", "state", "established"]).decode()
        )
    except Exception:
        return False


def require_successive_passes(
    check_func, server, port, successive_passes, successive_seconds
):
    for _ in range(successive_passes):
        if not check_func(server, port):
            return False
        time.sleep(successive_seconds)
    return True


# NOTE Revisit how much of the network stack we should restart. For now, I want to cover all
# cases of wifi and modems and ssh tunnel issues.
def restart_network_services():
    logging.warning("restarting network services")

    # ensure proper ownership of ports, ttyACM* for Modem
    ports = glob("/dev/ttyACM*")
    subprocess.run(["chown", "root:root"] + ports)
    subprocess.run(["chmod", "660"] + ports)

    # restart network services
    subprocess.run(
        [
            "systemctl",
            "restart",
            "NetworkManager",
            "ModemManager",
            "waggle-bk-reverse-tunnel",
        ]
    )


def reboot_os():
    logging.warning("rebooting the system")
    # aggressively but safely reboot the system
    subprocess.run(["systemctl", "--force", "reboot"])

def shutdown_os():
    logging.warning("shutting down the system")
    # aggressively but safely shutdown the system
    subprocess.run(["systemctl", "--force", "poweroff"])


# NOTE(sean) I'm trying to better isolate the full behavior of actions into self
# contained functions.
def build_rec_actions(nwwd_config):
    def reset_network_action():
        restart_network_services()
        increment_reset_file(nwwd_config.network_reset_file)

    def soft_reboot_action():
        resets = read_current_resets(nwwd_config.soft_reset_file)
        increment_reset_file(nwwd_config.soft_reset_file)
        if resets < nwwd_config.soft_num_resets:
            reboot_os()

    def hard_reboot_action():
        resets = read_current_resets(nwwd_config.hard_reset_file)
        increment_reset_file(nwwd_config.hard_reset_file)

        if resets < nwwd_config.hard_num_resets:
            shutdown_os()
        else:
            logging.warning("executing media switch recovery action")

            write_current_resets(nwwd_config.hard_reset_file, 0)
            write_current_resets(nwwd_config.soft_reset_file, 0)
            write_current_resets(nwwd_config.network_reset_file, 0)

            if int(nwwd_config.current_media) == 0:
                subprocess.run(["nvbootctrl", "set-active-boot-slot", "1"])
            else:
                subprocess.run(["nvbootctrl", "set-active-boot-slot", "0"])
	    
            reboot_os()

    # Recovery actions table [time (s), recovery function]
    # restart networking stack after 15, 20 and 25 of no beehive connectivity
    # reboot after 30 mins of no beehive connectivity shutdown after 1 hour 
    # of no connection to beehive after 3 30 minute reboots
    #
    # NOTE We sort in increasing order of threshold so that our linear
    # search finds the "earliest" available action
    recovery_actions = []

    for time in nwwd_config.network_resets:
        recovery_actions.append([time, reset_network_action])

    for time in nwwd_config.soft_resets:
        recovery_actions.append([time, soft_reboot_action])

    for time in nwwd_config.hard_resets:
        recovery_actions.append([time, hard_reboot_action])

    return sorted(recovery_actions)


def read_current_resets(reset_file):
    resets = 0
    if not Path(reset_file).exists():
        last_dir_index = reset_file.rfind("/")
        folder = reset_file[:last_dir_index]

        Path(folder).mkdir(parents=True, exist_ok=True)
	Path(reset_file).touch()

        with open(reset_file, 'w') as f:
            f.write('%d' % resets)
    else:
        with open(reset_file, 'r') as f:
            resets = int(f.readline())

    return resets


def write_current_resets(reset_file, current_resets):
    with open(reset_file, 'w') as f:
            f.write('%d' % current_resets)


def increment_reset_file(reset_file):
    resets = read_current_resets(reset_file)
    write_current_resets(reset_file, resets+1)


def update_reset_file(reset_file, value):
    file_value = read_current_resets(reset_file)
    if value != file_value:
        write_current_resets(reset_file, value)

def read_current_media():
        return 1 if '1' in subprocess.check_output(["nvbootctrl", "get-current-slot"]).decode() else 0

def main():
    logging.basicConfig(level=logging.INFO)

    nwwd_config = read_network_watchdog_config("/etc/waggle/nw/config.ini")
    wd_config = read_watchdog_config("/etc/waggle/config.ini")
    rssh_config = read_reverse_tunnel_config("/etc/waggle/config.ini")
    
    def health_check():
        logging.info("checking connection [%s:%s]", rssh_config.beekeeper_server, rssh_config.beekeeper_port)
        return require_successive_passes(
            ssh_connection_ok,
            rssh_config.beekeeper_server,
            rssh_config.beekeeper_port,
            nwwd_config.check_successive_passes,
            nwwd_config.check_successive_seconds,
        )

    def health_check_passed(timer):
        logging.info("connection ok")    
        update_reset_file(nwwd_config.hard_reset_file, 0)
        update_reset_file(nwwd_config.soft_reset_file, 0)
        update_reset_file(nwwd_config.network_reset_file, 0)

    def health_check_failed(timer):
        logging.warning("no connection for %ss", timer)

    recovery_actions = build_rec_actions(nwwd_config)

    watchdog = Watchdog(
        time_func=time.monotonic,
        health_check=health_check,
        health_check_passed=health_check_passed,
        health_check_failed=health_check_failed,
        recovery_actions=recovery_actions,
    )

    logging.info("marking boot as successful for media %s", nwwd_config.current_media)
    subprocess.run(["nv_update_engine", "-v"])

    while True:
        # update software watchdog
        update_systemd_watchdog()

        # update hardware watchdog
        if wd_config.nwwd_ok_file is not None:
            Path(wd_config.nwwd_ok_file).touch()

        watchdog.update()
        time.sleep(nwwd_config.check_seconds)


if __name__ == "__main__":
    main()
