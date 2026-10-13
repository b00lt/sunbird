"""Reads a Sigenergy SigenStor over Modbus TCP."""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Dict, List, Optional, Set

from pymodbus.client import AsyncModbusTcpClient

from .registers import INVERTER, PLANT, Reg, blocks, decode

log = logging.getLogger("sunbird.modbus")


class SigenModbus:
    def __init__(self, host: str, port: int = 502, plant_unit: int = 247,
                 inverter_units: Optional[List[int]] = None, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.plant_unit = plant_unit
        self.inverter_units = inverter_units if inverter_units is not None else [1]
        self.timeout = timeout
        self._client: Optional[AsyncModbusTcpClient] = None
        self._lock = asyncio.Lock()
        # (unit, address) of registers the device rejected; skipped afterwards
        self._unsupported: Set[tuple] = set()
        self._unit_kw: Optional[str] = None

    # pymodbus renamed slave= to device_id= in 3.10
    def _kw(self, client) -> str:
        if self._unit_kw is None:
            params = inspect.signature(client.read_input_registers).parameters
            self._unit_kw = "device_id" if "device_id" in params else "slave"
        return self._unit_kw

    async def _ensure(self) -> AsyncModbusTcpClient:
        if self._client is None or not self._client.connected:
            if self._client is not None:
                self._client.close()
            self._client = AsyncModbusTcpClient(
                self.host, port=self.port, timeout=self.timeout, retries=1)
            ok = await self._client.connect()
            if not ok:
                raise ConnectionError(f"Cannot connect to {self.host}:{self.port}")
            log.info("Connected to SigenStor at %s:%s", self.host, self.port)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def _read(self, unit: int, address: int, count: int) -> Optional[List[int]]:
        client = await self._ensure()
        kw = {self._kw(client): unit}
        rr = await client.read_input_registers(address, count=count, **kw)
        if rr.isError():
            return None
        return list(rr.registers)

    async def _read_group(self, unit: int, regs: List[Reg]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        regs = [r for r in regs if (unit, r.address) not in self._unsupported]
        for start, length, members in blocks(regs):
            words = await self._read(unit, start, length)
            if words is not None:
                for r in members:
                    off = r.address - start
                    v = decode(r, words[off:off + r.count])
                    if v is not None:
                        out[r.name] = v
                continue
            # Block rejected: fall back to one register at a time
            for r in members:
                w = await self._read(unit, r.address, r.count)
                if w is None:
                    log.info("Unit %s register %s (%s) not supported - skipping",
                             unit, r.address, r.name)
                    self._unsupported.add((unit, r.address))
                    continue
                v = decode(r, w)
                if v is not None:
                    out[r.name] = v
        return out

    async def poll(self) -> Dict[str, object]:
        """One full read. Raises on connection failure."""
        async with self._lock:
            try:
                plant = await self._read_group(self.plant_unit, PLANT)
                inverters = {}
                for u in self.inverter_units:
                    inverters[u] = await self._read_group(u, INVERTER)
            except Exception:
                await self.close()
                raise
        return {"plant": plant, "inverters": inverters}
