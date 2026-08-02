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
"""
┌─────────────────────────────────────────────────────────────────────┐
│                  PsuPoller — Two Independent Clocks                 │
└─────────────────────────────────────────────────────────────────────┘

BACKGROUND THREAD (daemon=True)              MAIN THREAD
  runs _poll_loop()                            runs your demo loop
  tick = POLL_INTERVAL_S = 0.5s                 tick = MAIN_WORK_INTERVAL_S = 2.0s

  t=0.0s ─ read PSU ─ put(sample_0) ──┐
  t=0.5s ─ read PSU ─ put(sample_1) ──┤
  t=1.0s ─ read PSU ─ put(sample_2) ──┼──►  [queue: 4 samples waiting]
  t=1.5s ─ read PSU ─ put(sample_3) ──┘            │
                                                    ▼
                                        t=2.0s ─ "do other work" (sleep)
                                                 then DRAIN queue:
                                                   get() → sample_0
                                                   get() → sample_1
                                                   get() → sample_2
                                                   get() → sample_3
                                                 print "drained 4 samples"

  t=2.0s ─ read PSU ─ put(sample_4) ──┐
  t=2.5s ─ read PSU ─ put(sample_5) ──┤
  t=3.0s ─ read PSU ─ put(sample_6) ──┼──►  [queue filling again]
  t=3.5s ─ read PSU ─ put(sample_7) ──┘            │
                                                    ▼
                                        t=4.0s ─ drain again → 4 more samples
                                        ...repeats...

  Ratio: MAIN_WORK_INTERVAL_S / POLL_INTERVAL_S = 2.0 / 0.5 = 4
  → main thread drains ~4 samples per cycle, proving both clocks
    run independently.


┌─────────────────────────────────────────────────────────────────────┐
│                    start() / stop() Control Flow                    │
└─────────────────────────────────────────────────────────────────────┘

  MAIN THREAD                          BACKGROUND THREAD
  ────────────                         ──────────────────
  poller.start()
    │
    ├─ _stop_event.clear()
    └─ Thread(target=_poll_loop,
              daemon=True).start() ───►  _poll_loop() begins
                                           │
                                           ▼
                                         while not stop_event.is_set():
                                           read → put(sample)
                                           stop_event.wait(POLL_INTERVAL_S)
                                              │
                                              │ (loops until stop_event set,
                                              │  or wakes early if set mid-wait)
                                              ▼
  poller.stop()
    │
    ├─ _stop_event.set() ───────────────►  wait() returns EARLY
    │                                       loop condition fails → exits
    └─ _thread.join(
         timeout=JOIN_TIMEOUT_S) ◄──────  thread exits, join() returns
         │
         ▼
    "poller stopped" printed


┌─────────────────────────────────────────────────────────────────────┐
│  KEY INSIGHT: JOIN_TIMEOUT_S is a SAFETY CEILING on stop(), not a    │
│  operating interval like the other two. It only matters if the      │
│  background thread is slow to exit (e.g. stuck on a slow PSU read). │
└─────────────────────────────────────────────────────────────────────┘
"""

import queue
import threading
import time

from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU
print("STEP 1: Import queue, threading, time, InstroPSU, SimulatedPSU completed")

VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"
SET_VOLTAGE = 5.0
SET_CURRENT = 1.0
CHANNEL = 1

print("STEP 2: Created VISA_RESOURCE for the simulated PSU , SET_VOLTAGE, SET_CURRENT, CHANNEL variables completed")

POLL_INTERVAL_S = 0.05       # how often the background thread reads the PSU, every 0.5s,
#the loop wakes up, takes a reading, puts it on the queue, then waits again. 
# Lower this and you get more samples per second (faster acquisition); 
# raise it and you get fewer, slower samples.

MAIN_WORK_INTERVAL_S = 3.0  # how often the main thread does its "other work" This constant just controls the demo pacing — 
#it's simulating "the main thread is busy doing something else" 
# so you can watch samples accumulate in the queue between drains. In a real script, 
# this wouldn't be a sleep — it'd be whatever actual work your main thread does (control logic, UI updates, etc.).

JOIN_TIMEOUT_S = 2.0

""" When you call stop(), it sets the stop event and then waits up to this long for the background thread to actually 
finish its current loop iteration and exit cleanly. It's a safety ceiling — if the thread doesn't exit within 2 seconds 
(e.g., it's stuck on a slow PSU read), join() gives up waiting rather than hanging your program forever, though the thread 
itself will still be a daemon and get killed when the process exits"""

print("STEP 3: Created POLL_INTERVAL_S, MAIN_WORK_INTERVAL_S, JOIN_TIMEOUT_S variables completed")


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

print("STEP 4: Created poll_loop function completed")

def drain_queue(sample_queue: "queue.Queue") -> list[dict]:
    """Pulls everything currently queued without blocking the caller."""
    samples = []
    while True:
        try:
            samples.append(sample_queue.get_nowait())
        except queue.Empty:
            break
    return samples
print("STEP 5: Created drain_queue function completed")

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
        print("STEP 6: Poller thread started, running in the background")
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
