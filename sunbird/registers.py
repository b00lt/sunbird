"""Sigenergy Modbus TCP register map (subset used by Sunbird).

Source: Sigenergy Modbus protocol, as implemented in the open-source
TypQxQ/Sigenergy-Local-Modbus Home Assistant integration.

All registers here are *input* registers (function code 0x04).
Value = raw / gain. Multi-register values are big-endian word order.

Plant (whole system, "SigenStor") lives on Modbus unit id 247.
Each inverter lives on its own unit id (default 1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Reg:
    name: str
    address: int
    count: int
    dtype: str  # u16 u32 u64 s16 s32
    gain: float = 1.0
    unit: str = ""


# ---- Plant registers (unit id 247) ---------------------------------------
PLANT: List[Reg] = [
    Reg("system_time", 30000, 2, "u32", 1, "s"),
    Reg("timezone_min", 30002, 1, "s16", 1, "min"),
    Reg("ems_work_mode", 30003, 1, "u16"),
    Reg("grid_sensor_status", 30004, 1, "u16"),
    # >0 buying from grid, <0 selling to grid
    Reg("grid_power", 30005, 2, "s32", 1000, "kW"),
    Reg("on_off_grid_status", 30009, 1, "u16"),
    Reg("ess_soc", 30014, 1, "u16", 10, "%"),
    Reg("phase_a_power", 30015, 2, "s32", 1000, "kW"),
    Reg("phase_b_power", 30017, 2, "s32", 1000, "kW"),
    Reg("phase_c_power", 30019, 2, "s32", 1000, "kW"),
    Reg("alarm1", 30027, 1, "u16"),
    Reg("alarm2", 30028, 1, "u16"),
    Reg("alarm3", 30029, 1, "u16"),
    Reg("alarm4", 30030, 1, "u16"),
    Reg("plant_active_power", 30031, 2, "s32", 1000, "kW"),
    Reg("pv_power", 30035, 2, "s32", 1000, "kW"),
    # >0 charging, <0 discharging
    Reg("ess_power", 30037, 2, "s32", 1000, "kW"),
    Reg("ess_max_charge_power", 30047, 2, "u32", 1000, "kW"),
    Reg("ess_max_discharge_power", 30049, 2, "u32", 1000, "kW"),
    Reg("running_state", 30051, 1, "u16"),
    Reg("grid_phase_a_power", 30052, 2, "s32", 1000, "kW"),
    Reg("grid_phase_b_power", 30054, 2, "s32", 1000, "kW"),
    Reg("grid_phase_c_power", 30056, 2, "s32", 1000, "kW"),
    Reg("ess_available_charge_kwh", 30064, 2, "u32", 100, "kWh"),
    Reg("ess_available_discharge_kwh", 30066, 2, "u32", 100, "kWh"),
    Reg("ess_rated_capacity_kwh", 30083, 2, "u32", 100, "kWh"),
    Reg("ess_charge_cutoff_soc", 30085, 1, "u16", 10, "%"),
    Reg("ess_discharge_cutoff_soc", 30086, 1, "u16", 10, "%"),
    Reg("ess_soh", 30087, 1, "u16", 10, "%"),
    Reg("total_pv_kwh", 30088, 4, "u64", 100, "kWh"),
    Reg("daily_consumed_kwh", 30092, 2, "u32", 100, "kWh"),
    Reg("total_consumed_kwh", 30094, 4, "u64", 100, "kWh"),
    Reg("third_party_pv_power", 30194, 2, "s32", 1000, "kW"),
    Reg("total_third_party_pv_kwh", 30196, 4, "u64", 100, "kWh"),
    Reg("total_battery_charge_kwh", 30200, 4, "u64", 100, "kWh"),
    Reg("total_battery_discharge_kwh", 30204, 4, "u64", 100, "kWh"),
    Reg("total_grid_import_kwh", 30216, 4, "u64", 100, "kWh"),
    Reg("total_grid_export_kwh", 30220, 4, "u64", 100, "kWh"),
    # Newer firmware only - silently skipped if unsupported
    Reg("daily_pv_kwh", 30272, 2, "u32", 100, "kWh"),
    Reg("yesterday_pv_kwh", 30274, 2, "u32", 100, "kWh"),
    Reg("general_load_power", 30282, 2, "s32", 1000, "kW"),
    Reg("total_load_power", 30284, 2, "s32", 1000, "kW"),
    Reg("ess_avg_cell_temp", 30286, 1, "s16", 10, "°C"),
]

# ---- Inverter registers (unit id 1 by default) ---------------------------
INVERTER: List[Reg] = [
    Reg("rated_power", 30540, 2, "u32", 1000, "kW"),
    Reg("rated_battery_kwh", 30548, 2, "u32", 100, "kWh"),
    Reg("ess_daily_charge_kwh", 30566, 2, "u32", 100, "kWh"),
    Reg("ess_daily_discharge_kwh", 30572, 2, "u32", 100, "kWh"),
    Reg("running_state", 30578, 1, "u16"),
    Reg("active_power", 30587, 2, "s32", 1000, "kW"),
    Reg("ess_power", 30599, 2, "s32", 1000, "kW"),
    Reg("battery_soc", 30601, 1, "u16", 10, "%"),
    Reg("battery_soh", 30602, 1, "u16", 10, "%"),
    Reg("battery_avg_cell_temp", 30603, 1, "s16", 10, "°C"),
    Reg("battery_avg_cell_voltage", 30604, 1, "u16", 1000, "V"),
    Reg("battery_max_temp", 30620, 1, "s16", 10, "°C"),
    Reg("battery_min_temp", 30621, 1, "s16", 10, "°C"),
    Reg("battery_max_cell_voltage", 30622, 1, "u16", 1000, "V"),
    Reg("battery_min_cell_voltage", 30623, 1, "u16", 1000, "V"),
    Reg("grid_frequency", 31002, 1, "u16", 100, "Hz"),
    Reg("internal_temp", 31003, 1, "s16", 10, "°C"),
    Reg("phase_a_voltage", 31011, 2, "u32", 100, "V"),
    Reg("phase_b_voltage", 31013, 2, "u32", 100, "V"),
    Reg("phase_c_voltage", 31015, 2, "u32", 100, "V"),
    Reg("phase_a_current", 31017, 2, "s32", 100, "A"),
    Reg("phase_b_current", 31019, 2, "s32", 100, "A"),
    Reg("phase_c_current", 31021, 2, "s32", 100, "A"),
    Reg("pv_string_count", 31025, 1, "u16"),
    Reg("pv1_voltage", 31027, 1, "s16", 10, "V"),
    Reg("pv1_current", 31028, 1, "s16", 100, "A"),
    Reg("pv2_voltage", 31029, 1, "s16", 10, "V"),
    Reg("pv2_current", 31030, 1, "s16", 100, "A"),
    Reg("pv3_voltage", 31031, 1, "s16", 10, "V"),
    Reg("pv3_current", 31032, 1, "s16", 100, "A"),
    Reg("pv4_voltage", 31033, 1, "s16", 10, "V"),
    Reg("pv4_current", 31034, 1, "s16", 100, "A"),
    Reg("pv_power", 31035, 2, "s32", 1000, "kW"),
    Reg("daily_pv_kwh", 31509, 2, "u32", 100, "kWh"),
]

EMS_MODES = {
    0: "Max self-consumption",
    1: "AI mode",
    2: "Time-of-use",
    5: "Full feed-in",
    6: "VPP scheduling",
    7: "Remote EMS",
    9: "Custom",
}

RUNNING_STATES = {
    0: "Standby",
    1: "Running",
    2: "Fault",
    3: "Shutdown",
    7: "Environment abnormal",
}

# "Not available" sentinels Sigenergy returns for unsupported values
_INVALID = {
    "u16": 0xFFFF,
    "s16": 0x7FFF,
    "u32": 0xFFFFFFFF,
    "s32": 0x7FFFFFFF,
    "u64": 0xFFFFFFFFFFFFFFFF,
}


def decode(reg: Reg, words: List[int]) -> Optional[float]:
    raw = 0
    for w in words:
        raw = (raw << 16) | (w & 0xFFFF)
    if raw == _INVALID.get(reg.dtype):
        return None
    if reg.dtype.startswith("s"):
        bits = 16 * reg.count
        if raw >= 1 << (bits - 1):
            raw -= 1 << bits
    val = raw / reg.gain if reg.gain != 1 else raw
    return round(val, 4) if isinstance(val, float) else val


def encode(reg: Reg, value: float) -> List[int]:
    """Inverse of decode - used by the simulator."""
    raw = int(round(value * reg.gain))
    bits = 16 * reg.count
    if raw < 0:
        raw += 1 << bits
    return [(raw >> (16 * (reg.count - 1 - i))) & 0xFFFF for i in range(reg.count)]


def blocks(regs: List[Reg], max_len: int = 120) -> List[Tuple[int, int, List[Reg]]]:
    """Group registers into contiguous read blocks (gaps break a block,
    because Sigenergy rejects reads that touch reserved addresses)."""
    out: List[Tuple[int, int, List[Reg]]] = []
    for r in sorted(regs, key=lambda r: r.address):
        if out:
            start, length, members = out[-1]
            if r.address == start + length and length + r.count <= max_len:
                out[-1] = (start, length + r.count, members + [r])
                continue
        out.append((r.address, r.count, [r]))
    return out


def by_name(regs: List[Reg]) -> Dict[str, Reg]:
    return {r.name: r for r in regs}
