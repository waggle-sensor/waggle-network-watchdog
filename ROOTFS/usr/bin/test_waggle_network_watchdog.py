#!/usr/bin/env python3
import logging
import sys


def main():
    import waggle_network_watchdog

    logging.basicConfig(level=logging.INFO)

    current_time = 0.0

    def fake_time():
        return current_time

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
        health_check=lambda: False,
        health_check_passed=real_watchdog.health_check_passed,
        health_check_failed=real_watchdog.health_check_failed,
        recovery_actions=real_watchdog.recovery_actions,
    )

    for _ in range(10000):
        current_time += 15.0
        watchdog.update()


if __name__ == "__main__":
    main()
