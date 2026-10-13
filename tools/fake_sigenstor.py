"""Fake SigenStor Modbus TCP server for testing without hardware.

    python tools/fake_sigenstor.py --port 5020 [--old-firmware]
    python -m sunbird --host 127.0.0.1   (with "port": 5020 in config.json)

Only the mapped registers exist; reads touching anything else get a Modbus
exception, just like the real device's reserved ranges.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymodbus.datastore as ds  # noqa: E402
from pymodbus.server import StartAsyncTcpServer  # noqa: E402

from sunbird.registers import INVERTER, PLANT, encode  # noqa: E402
from sunbird.simulator import SigenModel  # noqa: E402

NEW_FW_ONLY = {30272, 30274, 30282, 30284, 30286}


def make_block(regs, values):
    data = {}
    for r in regs:
        for i, w in enumerate(encode(r, values.get(r.name, 0))):
            data[r.address + i] = w
    return data


async def updater(model, plant_ctx, inv_ctx, plant_regs):
    last = time.time()
    while True:
        await asyncio.sleep(2)
        now = time.time()
        raw = model.step(now, now - last)
        last = now
        for addr, w in make_block(plant_regs, raw["plant"]).items():
            plant_ctx.setValues(4, addr, [w])
        for addr, w in make_block(INVERTER, raw["inverters"][1]).items():
            inv_ctx.setValues(4, addr, [w])


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--old-firmware", action="store_true")
    a = ap.parse_args()

    plant_regs = [r for r in PLANT if not (a.old_firmware and r.address in NEW_FW_ONLY)]
    model = SigenModel()
    raw = model.step(time.time(), 1)

    Device = getattr(ds, "ModbusDeviceContext", None) or getattr(ds, "ModbusSlaveContext")
    # pymodbus server contexts add 1 to the requested address internally
    def sparse(regs, values):
        return ds.ModbusSparseDataBlock({addr + 1: w for addr, w in make_block(regs, values).items()})

    plant_ctx = Device(ir=sparse(plant_regs, raw["plant"]))
    inv_ctx = Device(ir=sparse(INVERTER, raw["inverters"][1]))
    try:
        server_ctx = ds.ModbusServerContext(devices={247: plant_ctx, 1: inv_ctx}, single=False)
    except TypeError:
        server_ctx = ds.ModbusServerContext(slaves={247: plant_ctx, 1: inv_ctx}, single=False)

    asyncio.create_task(updater(model, plant_ctx, inv_ctx, plant_regs))
    print(f"Fake SigenStor listening on 0.0.0.0:{a.port}"
          f"{' (old firmware)' if a.old_firmware else ''}")
    await StartAsyncTcpServer(context=server_ctx, address=("0.0.0.0", a.port))


if __name__ == "__main__":
    asyncio.run(main())
