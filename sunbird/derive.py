"""Turns raw register values into one tidy snapshot for the dashboard."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .registers import EMS_MODES, RUNNING_STATES


def _f(d: Dict[str, Any], k: str) -> Optional[float]:
    v = d.get(k)
    return float(v) if v is not None else None


def build_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    p: Dict[str, Any] = raw.get("plant", {})
    invs: Dict[int, Dict[str, Any]] = raw.get("inverters", {})

    pv_sigen = _f(p, "pv_power") or 0.0
    pv_3rd = _f(p, "third_party_pv_power") or 0.0
    pv = pv_sigen + pv_3rd
    grid = _f(p, "grid_power") or 0.0          # + import, - export
    battery = _f(p, "ess_power") or 0.0        # + charging, - discharging

    load = _f(p, "general_load_power")
    if load is None:
        # Older firmware: load = Sigen AC output + grid + 3rd-party PV
        plant_ac = _f(p, "plant_active_power") or 0.0
        load = plant_ac + grid + pv_3rd
    load = max(0.0, load)

    daily_pv = _f(p, "daily_pv_kwh")
    if daily_pv is None and invs:
        vals = [_f(i, "daily_pv_kwh") for i in invs.values()]
        vals = [v for v in vals if v is not None]
        daily_pv = sum(vals) if vals else None

    def inv_sum(k):
        vals = [_f(i, k) for i in invs.values()]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    strings = []
    for unit, inv in invs.items():
        for n in range(1, 5):
            v, a = _f(inv, f"pv{n}_voltage"), _f(inv, f"pv{n}_current")
            if v is None or a is None:
                continue
            if v <= 0 and a <= 0 and n > (inv.get("pv_string_count") or 0):
                continue
            strings.append({"inverter": unit, "string": n, "voltage": v,
                            "current": a, "power": round(v * a / 1000, 3)})

    first_inv = next(iter(invs.values()), {})
    cap = _f(p, "ess_rated_capacity_kwh")
    dis_total = _f(p, "total_battery_discharge_kwh")
    cycles = round(dis_total / cap, 1) if cap and dis_total is not None else None
    alarms = [int(p.get(f"alarm{i}") or 0) for i in range(1, 5)]

    return {
        "ts": time.time(),
        "power": {"pv": round(pv, 3), "load": round(load, 3),
                  "battery": round(battery, 3), "grid": round(grid, 3),
                  "pv_third_party": round(pv_3rd, 3)},
        "battery": {
            "soc": _f(p, "ess_soc"),
            "soh": _f(p, "ess_soh") or _f(first_inv, "battery_soh"),
            "capacity_kwh": cap,
            "cycles": cycles,  # equivalent full cycles = lifetime discharged / capacity
            "lifetime_discharged_kwh": dis_total,
            "available_discharge_kwh": _f(p, "ess_available_discharge_kwh"),
            "available_charge_kwh": _f(p, "ess_available_charge_kwh"),
            "max_charge_kw": _f(p, "ess_max_charge_power"),
            "max_discharge_kw": _f(p, "ess_max_discharge_power"),
            "cutoff_charge_soc": _f(p, "ess_charge_cutoff_soc"),
            "cutoff_discharge_soc": _f(p, "ess_discharge_cutoff_soc"),
            "cell_temp": _f(p, "ess_avg_cell_temp") or _f(first_inv, "battery_avg_cell_temp"),
            "max_temp": _f(first_inv, "battery_max_temp"),
            "min_temp": _f(first_inv, "battery_min_temp"),
            "cell_v_avg": _f(first_inv, "battery_avg_cell_voltage"),
            "cell_v_max": _f(first_inv, "battery_max_cell_voltage"),
            "cell_v_min": _f(first_inv, "battery_min_cell_voltage"),
        },
        "totals": {  # lifetime counters, kWh
            "pv": (_f(p, "total_pv_kwh") or 0) + (_f(p, "total_third_party_pv_kwh") or 0)
            if p.get("total_pv_kwh") is not None else None,
            "load": _f(p, "total_consumed_kwh"),
            "import": _f(p, "total_grid_import_kwh"),
            "export": _f(p, "total_grid_export_kwh"),
            "charge": _f(p, "total_battery_charge_kwh"),
            "discharge": _f(p, "total_battery_discharge_kwh"),
        },
        "device_today": {  # day counters straight from the device
            "pv": daily_pv,
            "load": _f(p, "daily_consumed_kwh"),
            "charge": inv_sum("ess_daily_charge_kwh"),
            "discharge": inv_sum("ess_daily_discharge_kwh"),
            "pv_yesterday": _f(p, "yesterday_pv_kwh"),
        },
        "grid": {
            "phases_kw": [_f(p, "grid_phase_a_power"), _f(p, "grid_phase_b_power"),
                          _f(p, "grid_phase_c_power")],
            "voltages": [_f(first_inv, "phase_a_voltage"), _f(first_inv, "phase_b_voltage"),
                         _f(first_inv, "phase_c_voltage")],
            "frequency": _f(first_inv, "grid_frequency"),
            "off_grid": (p.get("on_off_grid_status") or 0) != 0,
        },
        "pv_strings": strings,
        "system": {
            "ems_mode": EMS_MODES.get(int(p["ems_work_mode"]), f"Mode {p['ems_work_mode']}")
            if p.get("ems_work_mode") is not None else None,
            "state": RUNNING_STATES.get(int(p["running_state"]), "Unknown")
            if p.get("running_state") is not None else None,
            "inverter_temp": _f(first_inv, "internal_temp"),
            "rated_power_kw": _f(first_inv, "rated_power"),
            "alarm": any(alarms),
            "alarm_codes": alarms,
        },
    }
