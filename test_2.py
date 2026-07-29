
#Runt the simulator before running this test. If you don't, you'll get a connection error. "python -m instro.psu.scpi_sim_server"
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

print("STEP 1: Import Instro & simulated PSU lib completed")

with InstroPSU(
    name="bench_psu",
    driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
    num_channels=2,
    
) as psu:

    print("STEP 2: driver created")
    print("STEP 3: psu object created")

    print("STEP 4: Voltage set, Current set and output enable succedded")

    psu.set_voltage(5.0, channel=1)
    psu.set_current_limit(1.0, channel=1)
    psu.output_enable(True, channel=1)

    voltage = psu.get_voltage(channel=1)
    print(f"V: {voltage.latest:.3f} V")

    print("STEP : Output not stopped, PSU not closed, Program stopped")