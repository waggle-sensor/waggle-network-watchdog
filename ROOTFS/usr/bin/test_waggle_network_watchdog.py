#!/usr/bin/env python3
import argparse
import logging
import random
import sys


def main():
    import waggle_network_watchdog

    parser = argparse.ArgumentParser(description="Network Watchdog Test")
    parser.add_argument(
        "-l", dest="loop", type=int, help="Current loop used for modifying pass rate"
    )
    parser.add_argument("-n", dest="loop_max", type=int, help="Max loop")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    den = args.loop_max
    logging.info("Loop %d (fail rate: %f)", args.loop, (args.loop / den))

    current_time = 0.0

    def fake_time():
        return current_time

    # simulate an increasing failure rate, based on loop
    def fake_health_check():
        return not (random.uniform(0, 1) < (args.loop / den))

    def fake_reboot_os():
        logging.warning("ACTION reboot os")
        sys.exit(0)

    def fake_shutdown_os():
        logging.warning("ACTION shutdown os")
        sys.exit(0)

    waggle_network_watchdog.reboot_os = fake_reboot_os
    waggle_network_watchdog.shutdown_os = fake_shutdown_os

    real_watchdog = waggle_network_watchdog.build_watchdog()

    watchdog = waggle_network_watchdog.Watchdog(
        time_func=fake_time,
        health_check=fake_health_check,
        health_check_passed=real_watchdog.health_check_passed,
        health_check_failed=real_watchdog.health_check_failed,
        recovery_actions=real_watchdog.recovery_actions,
        health_score_config=real_watchdog.health_score_config,
    )

    nwwd_config = waggle_network_watchdog.read_network_watchdog_config(
        waggle_network_watchdog.NW_WATCHDOG_CONFIG_PATH
    )
    waggle_network_watchdog.log_scoreboard(nwwd_config)

    for _ in range(10000):
        current_time += nwwd_config.health_check_period
        watchdog.update()


if __name__ == "__main__":
    main()
