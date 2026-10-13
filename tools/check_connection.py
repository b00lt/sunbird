"""First-run check: can we reach the SigenStor, and which inverter IDs answer?

    .venv/bin/python tools/check_connection.py [IP]
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sunbird.app import load_config  # noqa: E402
from sunbird.modbus_client import SigenModbus  # noqa: E402
from sunbird.registers import PLANT  # noqa: E402


async def main():
    cfg = load_config()
    host = sys.argv[1] if len(sys.argv) > 1 else cfg["host"]
    if not host:
        sys.exit("No SigenStor address yet. Run:  ./run.sh --check 192.168.x.x   (or set it in the dashboard under Settings)")
    port = cfg["port"]
    print(f"1) TCP port {port} on {host} ...", end=" ", flush=True)
    try:
        socket.create_connection((host, port), timeout=4).close()
        print("open ✓")
    except OSError as e:
        print(f"FAILED ({e})")
        print("   → Check the IP, that this computer is on the same network, and that your\n"
              "     installer has enabled Modbus-TCP on the SigenStor (and pressed SAVE).")
        return

    c = SigenModbus(host, port, cfg["plant_unit"], inverter_units=[])
    print(f"2) Plant data (device ID {cfg['plant_unit']}) ...", end=" ", flush=True)
    try:
        raw = await c._read_group(cfg["plant_unit"], PLANT)
    except Exception as e:
        print(f"FAILED ({e})")
        return
    if not raw:
        print("no answer ✗  (Modbus is open but the plant ID didn't respond)")
    else:
        print(f"{len(raw)} values ✓  SOC {raw.get('ess_soc')}%, PV {raw.get('pv_power')} kW, "
              f"grid {raw.get('grid_power')} kW")

    print("3) Looking for inverters on device IDs 1-10 ...")
    found = []
    for unit in range(1, 11):
        try:
            w = await c._read(unit, 30578, 1)  # inverter running state
        except Exception:
            w = None
        if w is not None:
            found.append(unit)
            print(f"   ID {unit}: inverter ✓")
    await c.close()
    if not found:
        print("   none found - PV string / battery cell details will be missing")
    elif found != cfg["inverter_units"]:
        print(f"\n   → Put this in config.json:  \"inverter_units\": {json.dumps(found)}")
    print("\nDone. Start the dashboard with ./run.sh")


asyncio.run(main())
