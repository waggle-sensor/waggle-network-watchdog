#!/usr/bin/env python3
import logging
import unittest
from waggle_network_watchdog import Watchdog


class TestWatchdog(unittest.TestCase):

    def test_healthy(self):
        watchdog = Watchdog(
            time_func=lambda: 0,
            health_check=lambda: True,
            health_check_passed=lambda timer: None,
            health_check_failed=lambda timer: None,
            recovery_actions=[],
        )
        watchdog.update()

    def test_simple(self):
        network_good = True

        def health_check():
            return network_good

        def health_check_passed(timer):
            pass

        def health_check_failed(timer):
            pass

        called = [0, 0]

        def action1():
            called[0] += 1

        def action2():
            called[1] += 1

        recovery_actions = [
            (50, action1),
            (100, action1),
            (200, action2),
        ]

        watchdog = Watchdog(
            health_check=health_check,
            health_check_passed=health_check_passed,
            health_check_failed=health_check_failed,
            recovery_actions=recovery_actions,
        )

        # simulate failing network
        network_good = False
        for _ in range(20):
            watchdog.tick(15)

        # restore network briefly
        network_good = True
        for _ in range(2):
            watchdog.tick(15)

        # simulate failing network again
        network_good = False
        for _ in range(20):
            watchdog.tick(15)

        self.assertTrue(all(c > 0 for c in called))


def main():
    logging.basicConfig(level=logging.INFO)

    network_good = True

    def health_check():
        return network_good

    def health_check_passed(timer):
        logging.info("connection ok")

    def health_check_failed(timer):
        logging.warning("connection failed at %ss seconds", timer)

    def action1():
        logging.info("action 1")

    def action2():
        logging.info("action 2")

    def action3():
        logging.info("action 3")

    recovery_actions = [
        (55, action1),
        (123, action2),
        (234, action3),
    ]

    current_time = 123

    def fake_time():
        return current_time

    watchdog = Watchdog(
        time_func=fake_time,
        health_check=health_check,
        health_check_passed=health_check_passed,
        health_check_failed=health_check_failed,
        recovery_actions=recovery_actions,
    )

    # simulate failing network
    network_good = False
    for _ in range(20):
        current_time += 15
        watchdog.update()

    # restore network briefly
    network_good = True
    for _ in range(2):
        current_time += 15
        watchdog.update()

    # simulate failing network again
    network_good = False
    for _ in range(20):
        current_time += 15
        watchdog.update()

if __name__ == "__main__":
    # unittest.main()
    main()
