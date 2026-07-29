
import time
print("STEP 1: Importing time for calculateing the delay completed")
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

print("STEP 2: Import Instro & simulated PSU lib completed")

SET_VOLTAGE = 5.0
SET_CURRENT = 1.0
CHANNEL = 1
NUM_SAMPLES = 100
SAMPLE_INTERVAL_S = 0.01

print("STEP 3: Values for Setting Voltage & current, Config num of sample, Confing time btw sample are completed")

with InstroPSU(
    name="bench_psu",
    driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
    num_channels=2,
) as psu:
    psu.set_voltage(SET_VOLTAGE, channel=CHANNEL)
    psu.set_current_limit(SET_CURRENT, channel=CHANNEL)
    psu.output_enable(True, channel=CHANNEL)

    print("STEP 4:Connect and configure")
    print("STEP 5:Opens the connection to the psu, then sends the three setpoints voltage, current and ch enable at once ")

    # Table header
    print("=" * 80)
    print(
        f"{'Sample':<8}{'Channel':<9}{'SetV(V)':<10}{'SetI(A)':<10}"
        f"{'ActualV(V)':<12}{'ActualI(A)':<12}"
    )
    print("=" * 80)

    print("STEP 6:Table Header created")

    sample = 0
    try:
        while True:
            sample += 1
            voltage = psu.get_voltage(channel=CHANNEL)
            current = psu.get_current(channel=CHANNEL)

            print(
                f"{sample:<8}{CHANNEL:<9}{SET_VOLTAGE:<10.3f}{SET_CURRENT:<10.3f}"
                f"{voltage.latest:<12.3f}{current.latest:<12.3f}"
            )

            time.sleep(SAMPLE_INTERVAL_S)
    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C).")

    psu.output_enable(False, channel=CHANNEL)

print("=" * 80)
print("Done.")
