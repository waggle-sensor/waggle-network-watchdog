#!/usr/bin/env python3
import ast
import configparser
import json
import logging
import socket
import subprocess
import time
from collections import deque
from glob import glob
from pathlib import Path
from typing import Callable, NamedTuple
import re

# NOTE We are going to use the nVME instead of SD card going forward, however, to keep the settings the same,
# we will continue calling this the SD.
MEDIA_RECOVERY = 0
MEDIA_PRIMARY = 1

NW_WATCHDOG_CONFIG_PATH = "/etc/waggle/nw/config.ini"
SYSTEM_CONFIG_PATH = "/etc/waggle/config.ini"


class Action(NamedTuple):
    thresh: int
    func: Callable


class HealthHistoryConfig(NamedTuple):
    health_check_history_count: int
    health_check_healthy_perc: float
    health_check_recovery_perc: float


class HealthHistory:
    def __init__(self, count: int):
        self.history = deque([False] * count, maxlen=count)
        # assume low score on init
        self.percentage = 0.0

    def add(self, n):
        self.history.append(n)
        self.percentage = self.history.count(True) / self.history.maxlen


class Watchdog:
    def __init__(
        self,
        time_func,
        recovery_actions,
        health_check,
        health_check_passed,
        health_check_failed,
        health_score_config,
    ):
        self.time_func = time_func
        self.recovery_actions = [
            Action(thresh, func) for thresh, func in recovery_actions
        ]
        # important: tick function expects recovery actions to sorted by thresh in increasing order
        self.recovery_actions.sort(key=lambda a: a.thresh)
        self.called_actions = set()
        self.health_check = health_check
        self.health_check_passed = health_check_passed
        self.health_check_failed = health_check_failed
        self.health_score_config = health_score_config
        self.health_score = HealthHistory(
            health_score_config.health_check_history_count
        )

        self.last_connection_time = self.time_func()

    def update(self):
        health_check_ok = self.health_check()
        health_check_finish_time = self.time_func()
        elapsed = health_check_finish_time - self.last_connection_time

        if health_check_ok:
            self.health_score.add(True)
            logging.info(
                "connection health check passed (%.2f%% >= %.2f%%)",
                100 * self.health_score.percentage,
                100 * self.health_score_config.health_check_healthy_perc,
            )
        else:
            self.health_score.add(False)
            logging.info(
                "connection health check failed (%.2f%% >= %.2f%%)",
                100 * self.health_score.percentage,
                100 * self.health_score_config.health_check_recovery_perc,
            )

        # if above the "healthy" percentage, indicate healthy
        if (
            self.health_score.percentage
            >= self.health_score_config.health_check_healthy_perc
        ):
            self.health_check_passed(elapsed)
            self.last_connection_time = health_check_finish_time
            self.called_actions.clear()

        # if below the "not healthy" percentage, enter recovery counter
        if (
            self.health_score.percentage
            < self.health_score_config.health_check_recovery_perc
        ):
            self.health_check_failed(elapsed)
            # dispatch all activated recovery actions
            for action in self.recovery_actions:
                if elapsed < action.thresh:
                    break
                if action in self.called_actions:
                    continue
                self.called_actions.add(action)
                logging.debug("calling action %s", action)
                # call a single action and break, allowing time for recovery
                action.func()
                break


class WatchdogConfig(NamedTuple):
    nwwd_ok_file: str


class NetworkWatchdogConfig(NamedTuple):
    health_check_period: float
    health_check_history: float
    health_check_healthy_perc: float
    health_check_recovery_perc: float
    current_media: int
    rssh_addrs: list
    network_services: list
    network_reset_start: int
    network_reset_interval: int
    network_reset_file: str
    soft_reset_start: int
    soft_num_resets: int
    soft_reset_file: str
    hard_reset_start: int
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

    logging.info(f"Config [all]: {all_settings}")
    logging.info(f"Config [network-reboot]: {network_reset_settings}")
    logging.info(f"Config [soft-reboot]: {soft_reset_settings}")
    logging.info(f"Config [hard-reboot]: {hard_reset_settings}")

    current_media = read_current_media()

    primary_storage_loc = ""
    if current_media == MEDIA_PRIMARY:
        # NOTE The sd_card_ is an unfortunate artifact of the MMC / SD naming but we will leave it for now
        primary_storage_loc = all_settings.get("sd_card_storage_loc", None)

    return NetworkWatchdogConfig(
        current_media=current_media,
        network_reset_start=json.loads(network_reset_settings.get("reset_start", 600)),
        network_reset_interval=json.loads(
            network_reset_settings.get("reset_interval", 300)
        ),
        network_reset_file=primary_storage_loc
        + network_reset_settings.get("current_reset_file", None),
        soft_reset_start=json.loads(soft_reset_settings.get("reset_start", 1800)),
        soft_num_resets=int(soft_reset_settings.get("max_resets", 0)),
        soft_reset_file=primary_storage_loc
        + soft_reset_settings.get("current_reset_file", None),
        hard_reset_start=json.loads(hard_reset_settings.get("reset_start", 3600)),
        hard_num_resets=int(hard_reset_settings.get("max_resets", 0)),
        hard_reset_file=primary_storage_loc
        + hard_reset_settings.get("current_reset_file", None),
        rssh_addrs=list(ast.literal_eval(all_settings.get("rssh_addrs", None))),
        network_services=json.loads(all_settings.get("network_services", None)),
        health_check_period=float(all_settings.get("health_check_period", 15.0)),
        health_check_history=float(all_settings.get("health_check_history", 600.0)),
        health_check_healthy_perc=float(
            all_settings.get("health_check_healthy_perc", 0.7)
        ),
        health_check_recovery_perc=float(
            all_settings.get("health_check_recovery_perc", 0.3)
        ),
    )


def read_reverse_tunnel_config(filename, section="reverse-tunnel"):
    d = read_config_section_dict(filename, section)

    return ReverseTunnelConfig(
        beekeeper_server=d.get("host", None),
        beekeeper_port=d.get("port", None),
    )


def log_scoreboard(nwconfig: NetworkWatchdogConfig):
    if nwconfig.current_media == MEDIA_PRIMARY:
        current_media_name = "primary"
    else:
        current_media_name = "recovery"

    logging.info("= Network Watchdog Scoreboard =")
    logging.info(f"Current Media:\t{nwconfig.current_media}\t{current_media_name}")
    logging.info(
        f"Network Reset Count:\t{read_current_resets(nwconfig.network_reset_file)}"
    )
    logging.info(f"Soft Reset Count:\t{read_current_resets(nwconfig.soft_reset_file)}")
    logging.info(f"Hard Reset Count:\t{read_current_resets(nwconfig.hard_reset_file)}")


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


def fix_modem_port_settings():
    ports = glob("/dev/ttyACM*")
    if len(ports) == 0:
        return
    # ensure proper ownership of ports, ttyACM* for Modem
    subprocess.run(["chown", "root:root"] + ports)
    subprocess.run(["chmod", "660"] + ports)


# NOTE Revisit how much of the network stack we should restart. For now, I want to cover all
# cases of wifi and modems and ssh tunnel issues.
def restart_network_services(nwwd_config):
    logging.warning("restarting network services")

    fix_modem_port_settings()

    # restart network services
    subprocess.run(["systemctl", "restart"] + nwwd_config.network_services)


def reboot_os_helper(nwwd_config):
    logging.warning("rebooting the system")
    log_scoreboard(nwwd_config)
    reboot_os()


def reboot_os():
    # execute normal reboot to allow shutdown services to clean-up
    subprocess.run(["systemctl", "reboot"])


def shutdown_os_helper(nwwd_config):
    logging.warning("shutting down the system")
    log_scoreboard(nwwd_config)
    shutdown_os()


def shutdown_os():
    # execute normal poweroff to allow shutdown services to clean-up
    subprocess.run(["systemctl", "poweroff"])


# NOTE(sean) I'm trying to better isolate the full behavior of actions into self
# contained functions.
def build_rec_actions(nwwd_config):
    def reset_network_action():
        restart_network_services(nwwd_config)
        increment_reset_file(nwwd_config.network_reset_file)

    def soft_reboot_action():
        resets = read_current_resets(nwwd_config.soft_reset_file)
        if resets < nwwd_config.soft_num_resets:
            increment_reset_file(nwwd_config.soft_reset_file)
            reboot_os_helper(nwwd_config)
        else:
            logging.info("skipping soft reboot, max reached")

    def hard_reboot_action():
        resets = read_current_resets(nwwd_config.hard_reset_file)
        increment_reset_file(nwwd_config.hard_reset_file)

        if resets < nwwd_config.hard_num_resets:
            shutdown_os_helper(nwwd_config)
        else:
            logging.warning("executing media switch recovery action")

            if int(nwwd_config.current_media) == MEDIA_RECOVERY:
                set_next_boot_media(MEDIA_PRIMARY)
            else:
                set_next_boot_media(MEDIA_RECOVERY)

            write_current_resets(nwwd_config.hard_reset_file, 0)
            write_current_resets(nwwd_config.soft_reset_file, 0)
            write_current_resets(nwwd_config.network_reset_file, 0)

            reboot_os_helper(nwwd_config)

    # Recovery actions table [time (s), recovery function]
    # NOTE We sort in increasing order of threshold so that our linear
    # search finds the "earliest" available action
    recovery_actions = []

    # add in the soft and hard reboot actions
    recovery_actions.append([nwwd_config.soft_reset_start, soft_reboot_action])
    recovery_actions.append([nwwd_config.hard_reset_start, hard_reboot_action])

    # we compute the number of network reset entries between the network restart
    #  start time and the last possible action start time
    last_action = min(nwwd_config.soft_reset_start, nwwd_config.hard_reset_start)
    for t in range(
        nwwd_config.network_reset_start, last_action, nwwd_config.network_reset_interval
    ):
        recovery_actions.append([t, reset_network_action])

    recovery_actions = sorted(recovery_actions, key=lambda x: x[0])
    logging.info(f"Recovery schedule: {recovery_actions}")

    return recovery_actions


def read_resets_safe(reset_file):
    try:
        with open(reset_file, "r") as f:
            return int(f.readline())
    except Exception:
        logging.warning("Unable to read from file: %s", reset_file)
        return 0


def write_resets_safe(reset_file, resets):
    try:
        with open(reset_file, "w") as f:
            f.write("%d" % resets)
    except Exception:
        logging.warning("Unable to write to file: %s", reset_file)


def read_current_resets(reset_file):
    resets = 0
    if not Path(reset_file).exists():
        last_dir_index = reset_file.rfind("/")
        folder = reset_file[:last_dir_index]

        Path(folder).mkdir(parents=True, exist_ok=True)
        Path(reset_file).touch()

        write_resets_safe(reset_file, resets)
    else:
        return read_resets_safe(reset_file)

    return resets


def write_current_resets(reset_file, current_resets):
    write_resets_safe(reset_file, current_resets)


def increment_reset_file(reset_file):
    resets = read_current_resets(reset_file)
    write_current_resets(reset_file, resets + 1)


def update_reset_file(reset_file, value):
    file_value = read_current_resets(reset_file)
    if value != file_value:
        write_current_resets(reset_file, value)


class BootInfo(NamedTuple):
    next: str
    current: str
    emmc: str
    nvme: str


def get_boot_info() -> BootInfo:
    output = subprocess.check_output(["efibootmgr"], text=True)

    match = re.search(r"BootNext: ([0-9A-F]+)", output)
    if match:
        next = match.group(1)
    else:
        next = ""

    match = re.search(r"BootCurrent: ([0-9A-F]+)", output)
    if match:
        current = match.group(1)
    else:
        raise RuntimeError("Could not detect current boot media.")

    match = re.search(r"Boot([0-9A-F]+).*eMMC", output)
    if match:
        emmc = match.group(1)
    else:
        raise RuntimeError("Could not detect eMMC boot media.")

    match = re.search(r"Boot([0-9A-F]+).*WDS100T3XHC", output)
    if match:
        nvme = match.group(1)
    else:
        raise RuntimeError("Could not detect nVME boot media.")

    return BootInfo(
        next=next,
        current=current,
        emmc=emmc,
        nvme=nvme,
    )


def read_current_media():
    boot_info = get_boot_info()

    if boot_info.current == boot_info.nvme:
        return MEDIA_PRIMARY

    if boot_info.current == boot_info.emmc:
        return MEDIA_RECOVERY

    raise RuntimeError("System is on unknown current media.")


def set_next_boot_media(target_media):
    boot_info = get_boot_info()

    if target_media == MEDIA_PRIMARY:
        next = boot_info.nvme
    elif target_media == MEDIA_RECOVERY:
        next = boot_info.emmc
    else:
        raise ValueError(f"Invalid target media {target_media}.")

    if boot_info.next != next:
        subprocess.check_call(["efibootmgr", "-n", next])


def build_watchdog(
    nwwd_config_path=NW_WATCHDOG_CONFIG_PATH, rssh_config_path=SYSTEM_CONFIG_PATH
):
    nwwd_config = read_network_watchdog_config(nwwd_config_path)
    rssh_config = read_reverse_tunnel_config(rssh_config_path)

    def publish_health(alias, health):
        try:
            subprocess.check_call(
                [
                    "waggle-publish-metric",
                    "sys.rssh_up",
                    str(int(health)),
                    "--meta",
                    "server=" + alias,
                ]
            )
        except Exception:
            logging.warning(
                "waggle-publish-metric not found. no metrics will be published"
            )

    def health_check():
        health = False
        # check the built in config(s)
        logging.info("checking connections to any of %s", nwwd_config.rssh_addrs)
        for alias, server, port in nwwd_config.rssh_addrs:
            curServerHealth = ssh_connection_ok(
                server,
                port,
            )

            health = health or curServerHealth
            logging.debug(f"Reporting ssh connection of {alias} as {curServerHealth}")

            publish_health(alias, curServerHealth)

        # check system "beekeeper" config
        logging.info(
            f"checking connections to 'beekeeper' [{rssh_config.beekeeper_server}, {rssh_config.beekeeper_port}]"
        )
        curServerHealth = ssh_connection_ok(
            rssh_config.beekeeper_server,
            rssh_config.beekeeper_port,
        )

        health = health or curServerHealth
        logging.debug(f"Reporting ssh connection of beekeeper as {curServerHealth}")

        publish_health("beekeeper", curServerHealth)

        return health

    def health_check_passed(timer):
        logging.info("connection ok (last healthy connection: %ss ago)", timer)
        update_reset_file(nwwd_config.hard_reset_file, 0)
        update_reset_file(nwwd_config.soft_reset_file, 0)
        update_reset_file(nwwd_config.network_reset_file, 0)

    def health_check_failed(timer):
        logging.warning("no connection for %ss (last healthy connection)", timer)

    recovery_actions = build_rec_actions(nwwd_config)

    health_check_history_count = int(
        nwwd_config.health_check_history / nwwd_config.health_check_period
    )
    logging.info(
        "Health history count: %d (history: %fs, check_period: %fs)",
        health_check_history_count,
        nwwd_config.health_check_history,
        nwwd_config.health_check_period,
    )

    health_score_config = HealthHistoryConfig(
        health_check_history_count=health_check_history_count,
        health_check_healthy_perc=nwwd_config.health_check_healthy_perc,
        health_check_recovery_perc=nwwd_config.health_check_recovery_perc,
    )

    return Watchdog(
        time_func=time.monotonic,
        health_check=health_check,
        health_check_passed=health_check_passed,
        health_check_failed=health_check_failed,
        recovery_actions=recovery_actions,
        health_score_config=health_score_config,
    )


def main():
    logging.basicConfig(level=logging.INFO)

    nwwd_config = read_network_watchdog_config(NW_WATCHDOG_CONFIG_PATH)
    wd_config = read_watchdog_config(SYSTEM_CONFIG_PATH)

    watchdog = build_watchdog(NW_WATCHDOG_CONFIG_PATH, SYSTEM_CONFIG_PATH)

    log_scoreboard(nwwd_config)

    while True:
        watchdog.update()

        # update software watchdog
        update_systemd_watchdog()

        # update hardware watchdog
        if wd_config.nwwd_ok_file is not None:
            Path(wd_config.nwwd_ok_file).touch()

        time.sleep(nwwd_config.health_check_period)


if __name__ == "__main__":
    main()
