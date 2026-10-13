"""Solar forecast per string from Forecast.Solar (free public API, no account).

https://api.forecast.solar/estimate/:lat/:lon/:dec/:az/:kwp
  dec = panel tilt (0 flat … 90 vertical)
  az  = -180..180, 0 = south, -90 = east, 90 = west
The free tier allows one plane per call and roughly 12 calls an hour per IP,
so each string is fetched once an hour (2 calls/hour for PV1 + PV2).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

log = logging.getLogger("sunbird.forecast")
API = "https://api.forecast.solar/estimate/{lat}/{lon}/{dec}/{az}/{kwp}"


def compass_to_api(deg: float) -> float:
    """Compass bearing (0 = north, 90 = east, 180 = south) → Forecast.Solar azimuth."""
    a = deg - 180
    return a - 360 if a > 180 else a


class Forecaster:
    def __init__(self, fcfg: Dict[str, Any], store, demo: bool = False):
        self.cfg = fcfg
        self.store = store
        self.demo = demo
        self.planes: List[Dict[str, Any]] = fcfg.get("planes", [])
        self.refresh = max(30, int(fcfg.get("refresh_minutes", 60))) * 60
        self.last_ok: Optional[float] = None
        self.error: Optional[str] = None
        self.place: Optional[str] = None

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "source": "demo" if self.demo else "Forecast.Solar",
                "last_ok": self.last_ok, "error": self.error, "place": self.place,
                "planes": [{"name": p["name"], "kwp": p["kwp"]} for p in self.planes]}

    # ---- real API ---------------------------------------------------------
    def _fetch(self, plane: Dict[str, Any]) -> Dict[str, Any]:
        url = API.format(lat=self.cfg["lat"], lon=self.cfg["lon"], dec=plane["tilt"],
                         az=round(plane["azimuth"], 1), kwp=plane["kwp"])
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Sunbird/0.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def _parse(self, plane: str, data: Dict[str, Any]):
        res = data.get("result") or {}
        info = (data.get("message") or {}).get("info") or {}
        self.place = info.get("place") or self.place
        tz = None
        if ZoneInfo and info.get("timezone"):
            try:
                tz = ZoneInfo(info["timezone"])
            except Exception:
                tz = None
        points = []
        for stamp, w in (res.get("watts") or {}).items():
            dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
            ts = dt.replace(tzinfo=tz).timestamp() if tz else dt.timestamp()  # naive = this computer's local time
            points.append((int(ts), float(w)))
        days = [(d, float(wh)) for d, wh in (res.get("watt_hours_day") or {}).items()]
        return points, days

    # ---- demo: a plausible clear-sky-ish curve per plane ---------------------
    def _fake(self, plane: Dict[str, Any]):
        rng = random.Random(hash(plane["name"]) & 0xFFFF)
        shift = math.sin(math.radians(plane["azimuth"])) * 1.6  # SE peaks earlier, SW later
        points, days = [], []
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for d in range(-21, 2):
            day = today + timedelta(days=d)
            factor = 0.45 + 0.5 * rng.random()
            wh = 0.0
            for q in range(0, 24 * 4):
                h = q / 4
                sunrise, sunset = 7.6, 17.9
                if not sunrise < h < sunset:
                    continue
                x = math.sin(math.pi * (h - sunrise) / (sunset - sunrise))
                x *= max(0.0, math.cos(math.radians((h - 12.8 - shift) * 12)))
                w = plane["kwp"] * 1000 * 0.42 * (x ** 1.5) * factor
                points.append((int((day + timedelta(hours=h)).timestamp()), round(w)))
                wh += w * 0.25
            days.append((day.strftime("%Y-%m-%d"), wh))
        return points, days

    async def refresh_once(self) -> bool:
        ok = True
        for p in self.planes:
            try:
                if self.demo:
                    points, days = self._fake(p)
                else:
                    data = await asyncio.to_thread(self._fetch, p)
                    points, days = self._parse(p["name"], data)
                self.store.save_forecast(p["name"], points, days)
            except urllib.error.HTTPError as e:
                ok = False
                self.error = "rate limited by Forecast.Solar - retrying later" if e.code == 429 else f"HTTP {e.code}"
                log.warning("Forecast %s: %s", p["name"], self.error)
            except Exception as e:  # network down etc. - keep the last forecast
                ok = False
                self.error = f"{type(e).__name__}: {e}"
                log.warning("Forecast %s failed: %s", p["name"], self.error)
            await asyncio.sleep(1.5)
        if ok:
            self.last_ok, self.error = time.time(), None
        return ok

    async def run(self):
        while True:
            ok = await self.refresh_once()
            await asyncio.sleep(self.refresh if ok else min(self.refresh, 900))
