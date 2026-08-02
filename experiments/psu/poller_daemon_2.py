"""
Poller Daemon #2 — instro's native background daemon
========================================================
Run the simulator before running this file. If you don't, you'll get a
connection error: "python -m instro.psu.scpi_sim_server"

This file exists purely as a comparison point to poller_daemon_1.py. Same
PSU, same channel config, same "poll in the background, drain samples on
the main thread" goal — but instead of a hand-rolled threading.Thread +
threading.Event + queue.Queue, it uses instro's built-in background
daemon (Instrument.start()/stop()/background_interval/get_channel(),
inherited by InstroPSU). No manual Thread/Event/Queue code here at all;
instro owns its own background thread, its own internal stop signal, and
its own internal channel buffer that plays the role our Queue played.

Mapping from file 1 to file 2:
  threading.Thread(target=poll_loop, daemon=True) + poller.start()
      -> psu.start()
  stop_event.set() + poller.join()
      -> psu.stop() (called automatically by psu.close(), i.e. on `with` exit)
  queue.Queue().get_nowait() / drain_queue()
      -> psu.get_single_channel_value(name)      (non-blocking latest value)
  a blocking queue.Queue().get()
      -> psu.get_channel(name, wait_for_new_samples=True)  (blocks for a new sample)

One auto-behavior worth knowing: entering the `with` block only calls
open() — it does NOT start the daemon. You must call psu.start()
yourself. Exiting the `with` block calls close(), which DOES stop the
daemon automatically (close() -> super().close() -> self.stop()).
"""

import time

from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"
SET_VOLTAGE = 5.0
SET_CURRENT = 1.0
CHANNEL = 1

POLL_INTERVAL_S = 0.5       # instro's background_interval — same role as file 1's POLL_INTERVAL_S
MAIN_WORK_INTERVAL_S = 2.0  # main thread's own independent timer, same as file 1


def main() -> None:
    psu = InstroPSU(
        name="bench_psu",
        driver=SimulatedPSU(VISA_RESOURCE),
        num_channels=2,
    )
    psu.background_interval = POLL_INTERVAL_S

    with psu:
        # __enter__ only opened the connection — the daemon isn't running yet.
        psu.start()

        psu.set_voltage(SET_VOLTAGE, channel=CHANNEL)
        psu.set_current_limit(SET_CURRENT, channel=CHANNEL)
        psu.output_enable(True, channel=CHANNEL)

        print("instro background daemon running. Main thread doing its own work.")
        print("Press Ctrl+C to stop.\n")

        next_work_at = time.monotonic() + MAIN_WORK_INTERVAL_S
        try:
            while True:
                if time.monotonic() >= next_work_at:
                    # Non-blocking latest-value pull — the counterpart to
                    # file 1's queue.get_nowait() drain.
                    voltage = psu.get_single_channel_value("bench_psu.ch1.voltage")
                    current = psu.get_single_channel_value("bench_psu.ch1.current")
                    print(f"[main] heartbeat — doing other work — latest V={voltage:.3f}  I={current:.3f}")
                    next_work_at = time.monotonic() + MAIN_WORK_INTERVAL_S

                time.sleep(0.05)  # small idle tick so this loop doesn't spin the CPU
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            psu.output_enable(False, channel=CHANNEL)

    # __exit__ -> close() -> super().close() -> self.stop() already
    # stopped the background daemon by this point.
    print("PSU closed (daemon stopped automatically by close()). Done.")


if __name__ == "__main__":
    main()


# What to try next:
#
# 1. Also try the blocking pull instead of the non-blocking one:
#        voltage = psu.get_channel("bench_psu.ch1.voltage", 1, wait_for_new_samples=True)
#    Notice this call will sit and wait for the *next* daemon sample rather
#    than returning whatever's most recent immediately — compare that to
#    get_single_channel_value's non-blocking behavior above, and to
#    file 1's queue.get() (blocking) vs get_nowait() (non-blocking) pair.
#
# 2. Compare the amount of code here to poller_daemon_1.py for the same
#    behavior. Then decide: for a real project, would you rather own the
#    Thread/Event/Queue plumbing yourself (more control, more to get wrong)
#    or lean on instro's daemon (less code, but you're bound to whatever
#    its start()/stop()/get_channel() contract lets you do)?
