"""
Poller Daemon #1 — DIY background thread with Event + Queue
=============================================================
Run the simulator before running this file. If you don't, you'll get a
connection error: "python -m instro.psu.scpi_sim_server"

This checkpoint wraps the synchronous InstroPSU polling from test_1.py-
test_4.py in a hand-rolled background thread, using threading.Event for
shutdown signaling and queue.Queue as the thread-safe handoff to the main
thread. Three concepts, three reasons they matter for real DAQ work:

daemon thread (daemon=True)
----------------------------
A "daemon" thread does not keep the Python process alive by itself — when
every non-daemon thread (including the main thread) has finished, Python
exits and kills any remaining daemon threads outright, no cleanup code
runs in them. That's exactly what you want for a poller: if the main
program is done, the poller shouldn't be the thing holding the process
open. The tradeoff is that a daemon thread can be killed mid-operation
(mid-write, mid-serial-transaction), so you still call stop() explicitly
during a *normal* shutdown rather than relying on daemon=True to clean up
for you — daemon=True is a safety net for abnormal exit, not a substitute
for an orderly stop.

threading.Event vs a raw bool flag
------------------------------------
A bool flag only gets noticed the next time the loop wakes up and checks
it — if the loop is sitting in time.sleep(interval), the stop request
waits out the rest of that sleep before anything happens. Event.wait(timeout)
does the same waiting, but returns immediately the moment another thread
calls .set() — the loop can stop mid-sleep instead of after it. For a fast
simulated poll interval this barely matters; for a slow real-hardware
poll (seconds between reads) it's the difference between Ctrl+C reacting
instantly and Ctrl+C appearing to "hang" for several seconds.

queue.Queue vs a shared list
-------------------------------
Queue.put()/get() are internally locked — the producer (poller thread)
and consumer (main thread) can push and pop at the same time without
either of them corrupting the other's view of the data, and get() can
block/timeout instead of you having to poll "is there anything new yet?"
in a spin loop. A plain list *often* looks fine under CPython today
because of how the GIL happens to serialize bytecode, but that's an
implementation detail, not a contract — Queue is the documented, correct
tool for producer/consumer handoff between threads.
"""

import queue
import threading
import time

from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"
SET_VOLTAGE = 5.0
SET_CURRENT = 1.0
CHANNEL = 1

POLL_INTERVAL_S = 0.5       # how often the background thread reads the PSU
MAIN_WORK_INTERVAL_S = 2.0  # how often the main thread does its "other work"
JOIN_TIMEOUT_S = 2.0


def poll_loop(psu: InstroPSU, sample_queue: "queue.Queue", stop_event: threading.Event) -> None:
    """Runs on the background thread. Reads the PSU and hands samples off via the queue."""
    while not stop_event.is_set():
        voltage = psu.get_voltage(channel=CHANNEL)
        current = psu.get_current(channel=CHANNEL)
        sample_queue.put(
            {
                "t": time.monotonic(),
                "voltage": voltage.latest if voltage is not None else None,
                "current": current.latest if current is not None else None,
            }
        )
        # wait() sleeps like time.sleep() would, but returns early the moment
        # stop_event.set() is called elsewhere — see docstring above.
        stop_event.wait(POLL_INTERVAL_S)


def drain_queue(sample_queue: "queue.Queue") -> list[dict]:
    """Pulls everything currently queued without blocking the caller."""
    samples = []
    while True:
        try:
            samples.append(sample_queue.get_nowait())
        except queue.Empty:
            break
    return samples


def main() -> None:
    sample_queue: "queue.Queue[dict]" = queue.Queue()
    stop_event = threading.Event()

    with InstroPSU(
        name="bench_psu",
        driver=SimulatedPSU(VISA_RESOURCE),
        num_channels=2,
    ) as psu:
        psu.set_voltage(SET_VOLTAGE, channel=CHANNEL)
        psu.set_current_limit(SET_CURRENT, channel=CHANNEL)
        psu.output_enable(True, channel=CHANNEL)

        poller = threading.Thread(
            target=poll_loop,
            args=(psu, sample_queue, stop_event),
            daemon=True,
            name="psu-poller",
        )
        poller.start()

        print("Poller running in the background. Main thread doing its own work.")
        print("Press Ctrl+C to stop.\n")

        next_work_at = time.monotonic() + MAIN_WORK_INTERVAL_S
        try:
            while True:
                # Proof the main thread isn't blocked on PSU I/O: it runs its
                # own independent timer instead of waiting on the poller.
                if time.monotonic() >= next_work_at:
                    pending = drain_queue(sample_queue)
                    print(f"[main] heartbeat — doing other work — {len(pending)} sample(s) collected since last check")
                    for sample in pending:
                        print(f"    V={sample['voltage']:.3f}  I={sample['current']:.3f}")
                    next_work_at = time.monotonic() + MAIN_WORK_INTERVAL_S

                time.sleep(0.05)  # small idle tick so this loop doesn't spin the CPU
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            stop_event.set()
            poller.join(timeout=JOIN_TIMEOUT_S)
            print(f"Poller thread alive after join: {poller.is_alive()}")
            psu.output_enable(False, channel=CHANNEL)

    print("PSU closed. Done.")


if __name__ == "__main__":
    main()


# What to try next:
#
# 1. Set POLL_INTERVAL_S = 0.05 and MAIN_WORK_INTERVAL_S = 3.0 (or vice versa)
#    and watch how many samples pile up in the queue between each "[main]
#    heartbeat" print — the two loops are fully decoupled, so one can run
#    much faster or slower than the other without either blocking.
#
# 2. Remove daemon=True from the Thread(...) call above, then run the script
#    and hit Ctrl+C. Compare what happens to the current clean shutdown path
#    (stop_event.set() + poller.join() finishes quickly either way, since we
#    stop it explicitly) versus killing the process before it reaches that
#    finally block (e.g. a second Ctrl+C, or a crash before the finally
#    block runs) — a non-daemon thread can keep the interpreter from exiting
#    until it's joined somewhere.
#
# 3. (stretch) Replace sample_queue with a plain Python list, appending from
#    poll_loop and popping from drain_queue. It will probably *look* fine in
#    quick testing — then think about why that's a coincidence of CPython's
#    GIL rather than a guarantee, and what a compound "check-then-pop" race
#    between two threads could do to a plain list that Queue's locking
#    prevents by design.
