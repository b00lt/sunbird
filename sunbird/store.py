"""SQLite history: one row per store interval, lifetime counters included
so daily energy can be computed exactly from counter deltas."""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

COUNTERS = ["pv_total", "load_total", "imp_total", "exp_total", "chg_total", "dis_total"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
  zone TEXT, ts INTEGER, te INTEGER, sek REAL, PRIMARY KEY (zone, ts)
);
CREATE TABLE IF NOT EXISTS forecast (
  ts INTEGER, plane TEXT, w REAL, fetched INTEGER, PRIMARY KEY (ts, plane)
);
CREATE TABLE IF NOT EXISTS forecast_day (
  date TEXT, plane TEXT, wh REAL, fetched INTEGER, PRIMARY KEY (date, plane)
);
CREATE TABLE IF NOT EXISTS samples (
  ts INTEGER PRIMARY KEY,
  pv REAL, load REAL, battery REAL, grid REAL, soc REAL,
  pv_total REAL, load_total REAL, imp_total REAL, exp_total REAL,
  chg_total REAL, dis_total REAL
);
"""


STRING_COLS = [f"s{n}_{k}" for n in range(1, 5) for k in ("p", "v", "a")]
TEMP_COLS = ["bt_avg", "bt_max", "bt_min"]
EXTRA_COLS = STRING_COLS + TEMP_COLS
BASE_COLS = ["ts", "pv", "load", "battery", "grid", "soc"] + COUNTERS
ALL_COLS = BASE_COLS + EXTRA_COLS
ENERGY_KEYS = ["pv", "load", "import", "export", "charge", "discharge"]


def home_sources(d: Dict[str, float]) -> Dict[str, float]:
    """Split one hour's home use into solar / battery / grid.
    Solar serves the home first, the battery covers what's left (its discharge may also be
    exported), and the grid supplies the rest - so grid energy that went into the battery is
    not counted as home use."""
    load = d.get("load") or 0.0
    solar = min(d.get("pv") or 0.0, load)
    battery = min(d.get("discharge") or 0.0, load - solar)
    return {"src_solar": solar, "src_battery": battery, "src_grid": max(0.0, load - solar - battery)}


class Store:
    def __init__(self, path: str, retention_days: int = 730, store_seconds: int = 30):
        self.path = path
        self.store_seconds = store_seconds
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(samples)")}
        for c in EXTRA_COLS:  # upgrade older databases in place
            if c not in have:
                self.db.execute(f"ALTER TABLE samples ADD COLUMN {c} REAL")
        self.db.commit()

    def add(self, snap: Dict[str, Any], ts: Optional[float] = None, commit: bool = True):
        p, t = snap["power"], snap["totals"]
        row = [int(ts or snap["ts"]), p["pv"], p["load"], p["battery"], p["grid"],
               snap["battery"]["soc"], t["pv"], t["load"], t["import"], t["export"],
               t["charge"], t["discharge"]]
        strings = (snap.get("pv_strings") or [])[:4]
        for n in range(4):
            st = strings[n] if n < len(strings) else None
            row += [st["power"], st["voltage"], st["current"]] if st else [None, None, None]
        b = snap["battery"]
        row += [b.get("cell_temp"), b.get("max_temp"), b.get("min_temp")]
        q = f"INSERT OR REPLACE INTO samples ({','.join(ALL_COLS)}) VALUES ({','.join('?' * len(ALL_COLS))})"
        with self._lock:
            self.db.execute(q, row)
            if commit:
                self.db.commit()

    def commit(self):
        with self._lock:
            self.db.commit()

    def last_row(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self.db.execute("SELECT * FROM samples ORDER BY ts DESC LIMIT 1")
            r = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], r)) if r else None

    def is_empty(self) -> bool:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0

    def prune(self):
        cutoff = time.time() - self.retention_days * 86400
        with self._lock:
            self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self.db.commit()

    def peaks(self, since: float) -> Dict[str, Optional[float]]:
        """Highest solar, home use, grid (either direction) and battery level since a time."""
        with self._lock:
            r = self.db.execute("SELECT MAX(pv), MAX(load), MAX(ABS(grid)), MAX(soc) FROM samples WHERE ts >= ?",
                                (int(since),)).fetchone()
        return {"pv": r[0], "load": r[1], "grid": r[2], "soc": r[3]}

    def history(self, hours: float, points: int = 400) -> List[Dict[str, Any]]:
        end = time.time()
        start = end - hours * 3600
        bucket = max(30, int(hours * 3600 / points))
        keys = ["pv", "load", "battery", "grid", "soc"] + EXTRA_COLS
        q = (f"SELECT (ts / ?) * ? AS b, {', '.join(f'AVG({k})' for k in keys)} "
             "FROM samples WHERE ts >= ? GROUP BY b ORDER BY b")
        with self._lock:
            rows = self.db.execute(q, (bucket, bucket, start)).fetchall()
        out = []
        for r in rows:
            d = {"t": r[0]}
            for k, v in zip(keys, r[1:]):
                if v is not None or k in ("pv", "load", "battery", "grid", "soc"):
                    d[k] = round(v, 4) if isinstance(v, float) else v
            out.append(d)
        return out

    def _counter_at(self, ts: float, before: bool) -> Optional[tuple]:
        cols = ",".join(COUNTERS)
        if before:
            q = f"SELECT {cols} FROM samples WHERE ts < ? AND pv_total IS NOT NULL ORDER BY ts DESC LIMIT 1"
        else:
            q = f"SELECT {cols} FROM samples WHERE ts >= ? AND pv_total IS NOT NULL ORDER BY ts ASC LIMIT 1"
        with self._lock:
            return self.db.execute(q, (ts,)).fetchone()

    def _last_before(self, ts: float) -> Optional[tuple]:
        cols = ",".join(COUNTERS)
        q = f"SELECT {cols} FROM samples WHERE ts < ? AND pv_total IS NOT NULL ORDER BY ts DESC LIMIT 1"
        with self._lock:
            return self.db.execute(q, (ts,)).fetchone()

    def daily(self, days: int) -> List[Dict[str, Any]]:
        """Energy per local calendar day from counter deltas.
        Baseline = last sample before midnight (or first sample of the day)."""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for i in range(days - 1, -1, -1):
            d0 = today - timedelta(days=i)
            d1 = d0 + timedelta(days=1)
            s, e = d0.timestamp(), d1.timestamp()
            with self._lock:
                has = self.db.execute("SELECT 1 FROM samples WHERE ts >= ? AND ts < ? LIMIT 1",
                                      (s, e)).fetchone()
            base = self._counter_at(s, before=True) or self._counter_at(s, before=False)
            end = self._last_before(e)
            if not has or not base or not end:
                out.append({"date": d0.strftime("%Y-%m-%d"), "pv": None, "load": None,
                            "import": None, "export": None, "charge": None, "discharge": None})
                continue
            vals = [max(0.0, (b2 or 0) - (b1 or 0)) for b1, b2 in zip(base, end)]
            out.append({"date": d0.strftime("%Y-%m-%d"),
                        "pv": vals[0], "load": vals[1], "import": vals[2],
                        "export": vals[3], "charge": vals[4], "discharge": vals[5]})
        return out

    # ---- energy per hour / day / week / month ---------------------------
    def _hourly(self, start: float):
        """Per clock-hour: max of each lifetime counter, summed string power, sample count."""
        cols = ", ".join(f"MAX({c})" for c in COUNTERS)
        scols = ", ".join(f"SUM(s{n}_p)" for n in range(1, 5))
        q = (f"SELECT ts / 3600 AS h, {cols}, {scols}, AVG(pv), AVG(load) "
             "FROM samples WHERE ts >= ? GROUP BY h ORDER BY h")
        with self._lock:
            return self.db.execute(q, (int(start) // 3600 * 3600,)).fetchall()

    def first_ts(self) -> Optional[int]:
        with self._lock:
            r = self.db.execute("SELECT MIN(ts) FROM samples").fetchone()
        return r[0] if r else None

    @staticmethod
    def _bucket(dt: datetime, group: str) -> datetime:
        if group == "hour":
            return dt.replace(minute=0, second=0, microsecond=0)
        d = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if group == "day":
            return d
        if group == "week":
            return d - timedelta(days=d.weekday())
        return d.replace(day=1)  # month

    @staticmethod
    def _step(b: datetime, group: str, k: int) -> datetime:
        if group == "hour":
            return b + timedelta(hours=k)
        if group == "day":
            return b + timedelta(days=k)
        if group == "week":
            return b + timedelta(weeks=k)
        m = b.month - 1 + k
        return b.replace(year=b.year + m // 12, month=m % 12 + 1)

    def energy(self, group: str, n: int) -> List[Dict[str, Any]]:
        """kWh per bucket. Counters give exact totals; PV strings are integrated
        from stored power samples. n=0 → everything since the first sample."""
        now = datetime.now()
        last = self._bucket(now, group)
        first_ts = self.first_ts()
        if first_ts is None:
            return []
        if n <= 0:
            first = self._bucket(datetime.fromtimestamp(first_ts), group)
        else:
            first = self._step(last, group, -(n - 1))
        keys: List[datetime] = []
        b = first
        while b <= last:
            keys.append(b)
            b = self._step(b, group, 1)
        acc: Dict[datetime, Dict[str, float]] = {k: {} for k in keys}
        rows = self._hourly(first.timestamp() - 3 * 3600)
        prev = [None] * len(COUNTERS)
        h_kwh = self.store_seconds / 3600.0
        for r in rows:
            h, counters, ssum = r[0], r[1:7], r[7:11]
            key = self._bucket(datetime.fromtimestamp(h * 3600), group)
            a = acc.get(key)
            dh: Dict[str, float] = {}
            for i, c in enumerate(counters):
                if c is None:
                    continue
                if prev[i] is not None:
                    dh[ENERGY_KEYS[i]] = max(0.0, c - prev[i])
                prev[i] = c
            if a is None:
                continue
            a["_n"] = 1
            for e, v in dh.items():
                a[e] = a.get(e, 0.0) + v
            if "load" in dh:   # where this hour's home energy came from (summed hour by hour)
                for e, v in home_sources(dh).items():
                    a[e] = a.get(e, 0.0) + v
            for j, sp in enumerate(ssum):
                if sp is not None:
                    a[f"s{j + 1}"] = a.get(f"s{j + 1}", 0.0) + sp * h_kwh
        out = []
        for k in keys:
            a = acc[k]
            has = bool(a.get("_n"))
            row = {"t": int(k.timestamp()), "key": k.strftime("%Y-%m-%d %H:00")}
            for e in ENERGY_KEYS + ["s1", "s2", "s3", "s4"]:
                row[e] = round(a[e], 3) if has and e in a else (0.0 if has and e in ENERGY_KEYS else None)
            if has and "src_grid" in a:
                for e in ("src_solar", "src_battery", "src_grid"):
                    row[e] = round(a[e], 3)
            out.append(row)
        return out

    def profile(self, days: int) -> List[Dict[str, Any]]:
        """Average solar and home power for each hour of the day."""
        rows = self._hourly(time.time() - days * 86400)
        sums = {h: [0.0, 0.0, 0] for h in range(24)}
        for r in rows:
            pv, load = r[11], r[12]
            if pv is None or load is None:
                continue
            hr = datetime.fromtimestamp(r[0] * 3600).hour
            s = sums[hr]
            s[0] += pv; s[1] += load; s[2] += 1
        return [{"hour": h, "pv": round(s[0] / s[2], 3) if s[2] else None,
                 "load": round(s[1] / s[2], 3) if s[2] else None} for h, s in sums.items()]

    # ---- solar forecast -------------------------------------------------
    def save_forecast(self, plane: str, points, days):
        now = int(time.time())
        with self._lock:
            self.db.executemany("INSERT OR REPLACE INTO forecast VALUES (?,?,?,?)",
                                [(ts, plane, w, now) for ts, w in points])
            self.db.executemany("INSERT OR REPLACE INTO forecast_day VALUES (?,?,?,?)",
                                [(d, plane, wh, now) for d, wh in days])
            self.db.commit()

    def forecast_points(self, start: float, end: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT ts, plane, w FROM forecast WHERE ts >= ? AND ts <= ? ORDER BY ts",
                                   (int(start), int(end))).fetchall()
        by: Dict[int, Dict[str, Any]] = {}
        for ts, plane, w in rows:
            d = by.setdefault(ts, {"t": ts})
            d[plane] = round(w / 1000, 3)
        out = sorted(by.values(), key=lambda d: d["t"])
        for d in out:
            d["total"] = round(sum(v for k, v in d.items() if k != "t"), 3)
        return out

    def forecast_days(self, first: str) -> Dict[str, Dict[str, float]]:
        with self._lock:
            rows = self.db.execute("SELECT date, plane, wh FROM forecast_day WHERE date >= ?", (first,)).fetchall()
        out: Dict[str, Dict[str, float]] = {}
        for d, plane, wh in rows:
            out.setdefault(d, {})[plane] = round(wh / 1000, 2)
        for v in out.values():
            v["total"] = round(sum(v.values()), 2)
        return out

    # ---- spot prices ----------------------------------------------------
    def save_prices(self, zone: str, rows):
        with self._lock:
            self.db.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?)",
                                [(zone, ts, te, sek) for ts, te, sek in rows])
            self.db.commit()

    def prices(self, zone: str, start: float, end: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT ts, te, sek FROM prices WHERE zone = ? AND ts >= ? AND ts < ? ORDER BY ts",
                                   (zone, int(start), int(end))).fetchall()
        return [{"t": r[0], "te": r[1], "sek": r[2]} for r in rows]
