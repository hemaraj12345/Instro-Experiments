#Runt the simulator before running this test. If you don't, you'll get a connection error. "python -m instro.psu.scpi_sim_server"
# the exception skips straight past psu.close() — your connection to the instrument is left open, dangling. 
# If you're testing repeatedly, this is exactly the kind of thing that causes "port already in use" or "resource busy" 
# errors on your next run.
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU
print("STEP 1: Import Instro & simulated PSU lib completed")
psu = InstroPSU(
    name="bench_psu",
    driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
    num_channels=2,
)
print("STEP 2: driver created")
print("STEP 3: psu object created")
psu.open()
print("STEP 4: psu.open() succeeded")
psu.set_voltage(5.0, channel=1)
psu.set_current_limit(1.0, channel=1)
psu.output_enable(True, channel=1)
print("STEP 5: Voltage set, Current set and output enable succedded")
voltage = psu.get_voltage(channel=1)
print(f"V: {voltage.latest:.3f} V")
psu.output_enable(False, channel=1)
print("STEP 6: output Disable succedded")
psu.close()
print("STEP 7: PSU closed")