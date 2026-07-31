"""
PSU Live Logger
===============
Runs the simulated PSU and publishes every measurement to two destinations:
  1. Console  — custom ConsolePrinter (no install required)
  2. JSONL file — instro FilePublisher

Also starts a tiny HTTP server so psu_dashboard.html can poll live data.

Usage
-----
Terminal 1 – start the SCPI simulator:
    python -m instro.psu.scpi_sim_server

Terminal 2 – start this logger:
    python psu_live_logger.py

Then open http://localhost:8765 in your browser for the live chart.
Press Ctrl+C in Terminal 2 to stop.
"""

import time
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

print("STEP 1: Import time, threading, datetime, HTTPServer, BaseHTTPRequestHandler, Path completed")

from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU
from instro.lib.publishers.files import FilePublisher
from instro.lib.types import Measurement, Command

print("STEP 2: Import InstroPSU, SimulatedPSU, FilePublisher, Measurement, Command completed")


# ── Config ───────────────────────────────────────────────────────────────────

DATA_DIR          = Path(__file__).parent / "data"
JSONL_FILE_NAME   = "psu_live"          # → data/psu_live.jsonl
SET_VOLTAGE       = 5.0                 # V
SET_CURRENT_LIMIT = 1.0                 # A
OVP_LEVEL         = 5.5                 # V  (overvoltage protection)
CHANNEL           = 1
SAMPLE_INTERVAL_S = 0.5                 # seconds between reads
HTTP_PORT         = 8765

print("STEP 3: Created config variables: DATA_DIR, JSONL_FILE_NAME, SET_VOLTAGE, SET_CURRENT_LIMIT, OVP_LEVEL, CHANNEL, SAMPLE_INTERVAL_S, HTTP_PORT")
# ── Custom console publisher ──────────────────────────────────────────────────

class ConsolePrinter:
    """
    Implements the Publisher protocol (duck-typed).
    Prints each Measurement to stdout in a readable table row.
    Commands are shown in a lighter format.
    """

    def publish(self, data: "Measurement | Command", **kwargs) -> None:
        print("STEP 4: Starting publish process")
        if isinstance(data, Measurement):
            # Timestamps are nanoseconds since epoch
            ts_ns = data.timestamps[-1] if data.timestamps else 0
            dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            for ch, vals in data.channel_data.items():
                print(f"  [{dt}]  {ch:<48}  {vals[-1]:>10.4f}")
        elif isinstance(data, Command):
            ts_ns = data.timestamp
            dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            for ch, val in data.channel_data.items():
                print(f"  [{dt}]  CMD  {ch:<43}  {val!r:>10}")

    def close(self) -> None:
        pass


# ── HTTP server ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = Path(__file__).parent / "psu_dashboard.html"
print("STEP 5: Created dashboard HTML path")

class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the dashboard HTML and the live JSONL data file."""
    print("STEP 6: Created dashboard handler")

    jsonl_path: Path  # injected before server starts

    def log_message(self, *args):
        """Silence per-request access logs so they don't mix with console output."""
        pass

    def do_GET(self):
        if self.path == "/data":
            # Return the full JSONL file so the browser can diff against its
            # previous copy and render any new lines.
            try:
                content = self.jsonl_path.read_bytes()
            except FileNotFoundError:
                content = b""
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)

        elif self.path in ("/", "/index.html", "/dashboard"):
            try:
                html = DASHBOARD_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_error(404, "psu_dashboard.html not found next to this script")

        else:
            self.send_error(404)


def _start_http_server(jsonl_path: Path) -> None:
    print("STEP 7: Starting HTTP server")
    DashboardHandler.jsonl_path = jsonl_path
    server = HTTPServer(("localhost", HTTP_PORT), DashboardHandler)
    server.serve_forever()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    DATA_DIR.mkdir(exist_ok=True)
    jsonl_path = DATA_DIR / f"{JSONL_FILE_NAME}.jsonl"

    # Delete any leftover file from a previous run so the chart starts fresh.
    if jsonl_path.exists():
        jsonl_path.unlink()

    print("STEP 4: Starting publish process")
    file_pub    = FilePublisher(directory=DATA_DIR, format="jsonl", custom_file_name=JSONL_FILE_NAME)
    console_pub = ConsolePrinter()

    # Start HTTP server in the background before opening the instrument.
    http_thread = threading.Thread(
        target=_start_http_server,
        args=(jsonl_path,),
        daemon=True,  # exits automatically when the main thread exits
    )
    http_thread.start()

    print("=" * 60)
    print("  PSU Live Logger")
    print("=" * 60)
    print(f"  JSONL  →  {jsonl_path}")
    print(f"  Dashboard → http://localhost:{HTTP_PORT}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    print()

    with InstroPSU(
        name="bench_psu",
        driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
        num_channels=2,
        publishers=[console_pub, file_pub],
    ) as psu:

        # Configure channel 1
        psu.set_voltage(SET_VOLTAGE, channel=CHANNEL)
        psu.set_current_limit(SET_CURRENT_LIMIT, channel=CHANNEL)
        psu.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)
        psu.set_overvoltage_protection_enabled(True, channel=CHANNEL)
        psu.output_enable(True, channel=CHANNEL)

        print(f"  Channel {CHANNEL} enabled: {SET_VOLTAGE} V / {SET_CURRENT_LIMIT} A limit")
        print()

        sample = 0
        try:
            while True:
                sample += 1
                # Both calls auto-publish to console_pub AND file_pub
                # because @publish_measurement is applied to these methods.
                psu.get_voltage(channel=CHANNEL)
                psu.get_current(channel=CHANNEL)
                time.sleep(SAMPLE_INTERVAL_S)

        except KeyboardInterrupt:
            print(f"\n  Stopped after {sample} samples.")

        finally:
            psu.output_enable(False, channel=CHANNEL)
            print("  Output disabled. Goodbye.")


if __name__ == "__main__":
    main()
