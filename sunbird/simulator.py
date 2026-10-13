"""A plausible SigenStor model, used for --demo mode and the fake Modbus server.

Produces the same raw register dict that SigenModbus.poll() returns, so the
whole pipeline downstream is exercised identically.
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime
from typing import Any, Dict


class SigenModel:
    def __init__(self, pv_kwp: float = 10.0, battery_kwh: float = 16.0,
                 max_batt_kw: float = 5.0, seed: int = 7):
        self.rng = random.Random(seed)
        self.pv_kwp = pv_kwp
        self.cap = battery_kwh
        self.max_batt = max_batt_kw
        self.soc = 46.0
        self.cloud = 0.8
        self.spike_until = 0.0
        self.spike_kw = 0.0
        self.t = {"pv": 18432.5, "load": 21876.2, "imp": 9120.4, "exp": 5410.9,
                  "chg": 3311.0, "dis": 3012.7}
        self.day = None
        self.daily = {"pv": 0.0, "load": 0.0, "chg": 0.0, "dis": 0.0}
        self.yesterday_pv = 11.4

    def _pv(self, ts: float) -> float:
        d = datetime.fromtimestamp(ts)
        h = d.hour + d.minute / 60 + d.second / 3600
        sunrise, sunset = 7.6, 17.9
        if not sunrise < h < sunset:
            return 0.0
        x = math.sin(math.pi * (h - sunrise) / (sunset - sunrise))
        self.cloud += self.rng.gauss(0, 0.02)
        self.cloud = min(1.0, max(0.25, self.cloud + (0.6 - self.cloud) * 0.01))
        return self.pv_kwp * 0.42 * (x ** 1.8) * self.cloud

    def _load(self, ts: float) -> float:
        d = datetime.fromtimestamp(ts)
        h = d.hour + d.minute / 60
        base = 0.5 + 0.25 * (1 + math.cos(math.pi * h / 12)) / 2  # heat pump, colder at night
        base += 0.9 * math.exp(-((h - 7.3) ** 2) / 0.8)    # morning
        base += 1.4 * math.exp(-((h - 18.5) ** 2) / 2.0)   # dinner
        base += 0.35 * math.exp(-((h - 21.5) ** 2) / 1.5)
        if ts > self.spike_until and self.rng.random() < 0.004:
            self.spike_kw = self.rng.choice([1.8, 2.2, 1.2, 3.0])
            self.spike_until = ts + self.rng.uniform(90, 900)
        spike = self.spike_kw if ts < self.spike_until else 0.0
        return max(0.15, base + spike + self.rng.gauss(0, 0.04))

    def step(self, ts: float, dt: float) -> Dict[str, Any]:
        day = datetime.fromtimestamp(ts).date()
        if self.day != day:
            if self.day is not None:
                self.yesterday_pv = self.daily["pv"]
            self.day = day
            self.daily = {k: 0.0 for k in self.daily}

        pv = self._pv(ts)
        load = self._load(ts)
        surplus = pv - load
        batt = 0.0
        if surplus > 0 and self.soc < 100:
            batt = min(surplus, self.max_batt, (100 - self.soc) / 100 * self.cap * 3600 / dt)
        elif surplus < 0 and self.soc > 10:
            batt = -min(-surplus, self.max_batt, (self.soc - 10) / 100 * self.cap * 3600 / dt)
        grid = load - pv + batt
        h = dt / 3600
        self.soc = min(100.0, max(0.0, self.soc + batt * h / self.cap * 100))

        self.t["pv"] += pv * h
        self.t["load"] += load * h
        self.t["imp"] += max(grid, 0) * h
        self.t["exp"] += max(-grid, 0) * h
        self.t["chg"] += max(batt, 0) * h
        self.t["dis"] += max(-batt, 0) * h
        self.daily["pv"] += pv * h
        self.daily["load"] += load * h
        self.daily["chg"] += max(batt, 0) * h
        self.daily["dis"] += max(-batt, 0) * h

        s1 = pv * 0.52
        s2 = pv - s1
        v1 = 395 + self.rng.gauss(0, 3) if pv > 0.02 else 0.0
        v2 = 388 + self.rng.gauss(0, 3) if pv > 0.02 else 0.0
        plant = {
            "system_time": int(ts), "timezone_min": 120, "ems_work_mode": 0,
            "grid_sensor_status": 1, "grid_power": grid, "on_off_grid_status": 0,
            "ess_soc": round(self.soc, 1),
            "grid_phase_a_power": grid * 0.36, "grid_phase_b_power": grid * 0.31,
            "grid_phase_c_power": grid * 0.33,
            "alarm1": 0, "alarm2": 0, "alarm3": 0, "alarm4": 0,
            "plant_active_power": pv - batt, "pv_power": pv, "ess_power": batt,
            "ess_max_charge_power": self.max_batt, "ess_max_discharge_power": self.max_batt,
            "running_state": 1,
            "phase_a_power": (pv - batt) / 3, "phase_b_power": (pv - batt) / 3,
            "phase_c_power": (pv - batt) / 3,
            "ess_available_charge_kwh": (100 - self.soc) / 100 * self.cap,
            "ess_available_discharge_kwh": max(0, self.soc - 10) / 100 * self.cap,
            "ess_rated_capacity_kwh": self.cap, "ess_charge_cutoff_soc": 100,
            "ess_discharge_cutoff_soc": 10, "ess_soh": 99.2,
            "total_pv_kwh": self.t["pv"], "daily_consumed_kwh": self.daily["load"],
            "total_consumed_kwh": self.t["load"],
            "third_party_pv_power": 0.0, "total_third_party_pv_kwh": 0.0,
            "total_battery_charge_kwh": self.t["chg"],
            "total_battery_discharge_kwh": self.t["dis"],
            "total_grid_import_kwh": self.t["imp"], "total_grid_export_kwh": self.t["exp"],
            "daily_pv_kwh": self.daily["pv"], "yesterday_pv_kwh": self.yesterday_pv,
            "general_load_power": load, "total_load_power": load,
            "ess_avg_cell_temp": 21.5 + abs(batt) * 0.6,
        }
        inv = {
            "rated_power": 10.0, "rated_battery_kwh": self.cap,
            "ess_daily_charge_kwh": self.daily["chg"],
            "ess_daily_discharge_kwh": self.daily["dis"],
            "running_state": 1, "active_power": pv - batt, "ess_power": batt,
            "battery_soc": round(self.soc, 1), "battery_soh": 99.2,
            "battery_avg_cell_temp": 21.5 + abs(batt) * 0.6,
            "battery_avg_cell_voltage": 3.28 + self.soc / 1000,
            "battery_max_temp": 23.0 + abs(batt) * 0.7, "battery_min_temp": 20.4,
            "battery_max_cell_voltage": 3.30 + self.soc / 1000,
            "battery_min_cell_voltage": 3.26 + self.soc / 1000,
            "grid_frequency": 50.0 + self.rng.gauss(0, 0.015),
            "internal_temp": 31 + pv * 1.2,
            "phase_a_voltage": 231 + self.rng.gauss(0, 0.8),
            "phase_b_voltage": 230 + self.rng.gauss(0, 0.8),
            "phase_c_voltage": 232 + self.rng.gauss(0, 0.8),
            "phase_a_current": (pv - batt) / 3 / 0.231, "phase_b_current": (pv - batt) / 3 / 0.230,
            "phase_c_current": (pv - batt) / 3 / 0.232,
            "pv_string_count": 2,
            "pv1_voltage": v1, "pv1_current": s1 * 1000 / v1 if v1 else 0.0,
            "pv2_voltage": v2, "pv2_current": s2 * 1000 / v2 if v2 else 0.0,
            "pv3_voltage": 0.0, "pv3_current": 0.0, "pv4_voltage": 0.0, "pv4_current": 0.0,
            "pv_power": pv, "daily_pv_kwh": self.daily["pv"],
        }
        return {"plant": plant, "inverters": {1: inv}}


class DemoSource:
    """Drop-in replacement for SigenModbus that runs the model in real time."""

    def __init__(self):
        self.model = SigenModel()
        self.last = time.time()

    def backfill(self, start_ts: float, step: float):
        """Yield raw dicts from start_ts until now, for seeding history."""
        ts = start_ts
        now = time.time()
        while ts < now:
            yield ts, self.model.step(ts, step)
            ts += step
        self.last = now

    async def poll(self):
        now = time.time()
        dt = max(0.5, now - self.last)
        self.last = now
        return self.model.step(now, dt)

    async def close(self):
        pass
