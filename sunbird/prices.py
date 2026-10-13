"""Nord Pool day-ahead spot prices for SE1–SE4 via elprisetjustnu.se (free, open).

https://www.elprisetjustnu.se/api/v1/prices/YYYY/MM-DD_SE3.json
→ [{"SEK_per_kWh": 1.23, "EUR_per_kWh": .., "EXR": .., "time_start": iso, "time_end": iso}, ...]
Prices are excl. moms, fees and taxes; 15-minute slots since 2025-10-01.
Tomorrow's prices are published from about 13:00.
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger("sunbird.prices")
API = "https://www.elprisetjustnu.se/api/v1/prices/{y}/{md}_{zone}.json"
ZONES = ("SE1", "SE2", "SE3", "SE4")


class Prices:
    def __init__(self, store, demo: bool = False):
        self.store = store
        self.demo = demo
        self._miss: Dict[tuple, float] = {}   # (zone, day) -> time of last failed/404 fetch
        self.error: Optional[str] = None

    def _fetch(self, zone: str, d: date) -> Optional[List[Dict[str, Any]]]:
        url = API.format(y=d.year, md=d.strftime("%m-%d"), zone=zone)
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Sunbird/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None          # not published yet
            raise
        out = []
        for row in data:
            ts = datetime.fromisoformat(row["time_start"]).timestamp()
            te = datetime.fromisoformat(row["time_end"]).timestamp()
            out.append((int(ts), int(te), float(row["SEK_per_kWh"])))
        return out

    def _fake(self, zone: str, d: date):
        rng = random.Random(f"{zone}{d.isoformat()}")
        base = {"SE1": 0.25, "SE2": 0.28, "SE3": 0.65, "SE4": 0.95}[zone] * (0.7 + 0.6 * rng.random())
        start = datetime(d.year, d.month, d.day)
        out = []
        for q in range(96):
            h = q / 4
            shape = 1 + 0.55 * math.exp(-((h - 8) ** 2) / 3) + 0.8 * math.exp(-((h - 18.5) ** 2) / 4) - 0.35 * math.exp(-((h - 3) ** 2) / 6)
            p = max(0.01, base * shape + rng.gauss(0, 0.04 * base))
            ts = (start + timedelta(minutes=15 * q)).timestamp()
            out.append((int(ts), int(ts + 900), round(p, 4)))
        return out

    def day(self, zone: str, d: date) -> List[Dict[str, Any]]:
        """Prices for one local day; fetched once and kept in the database."""
        start = datetime(d.year, d.month, d.day).timestamp()
        rows = self.store.prices(zone, start, start + 86400)
        if len(rows) >= 23:  # 24 hourly or ~96 quarter-hour slots
            return rows
        key = (zone, d.isoformat())
        if time.time() - self._miss.get(key, 0) < 1800:  # don't hammer: retry at most every 30 min
            return rows
        try:
            fetched = self._fake(zone, d) if self.demo else self._fetch(zone, d)
            self.error = None
        except Exception as e:
            fetched = None
            self.error = f"{type(e).__name__}: {e}"
            log.warning("Price fetch %s %s failed: %s", zone, d, self.error)
        if not fetched:
            self._miss[key] = time.time()
            return rows
        self.store.save_prices(zone, fetched)
        return self.store.prices(zone, start, start + 86400)
