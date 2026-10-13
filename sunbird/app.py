"""Sunbird web server: polls the SigenStor, stores history, serves the dashboard."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .derive import build_snapshot
from .store import Store

log = logging.getLogger("sunbird")
ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CONFIG_PATH = Path(os.environ.get("SUNBIRD_CONFIG", ROOT / "config.json"))

# Battery modules users can pick in Settings (capacity per module).
BATTERY_MODELS = [
    {"id": "BAT 5.0", "kwh": 5.0},
    {"id": "BAT 8.0", "kwh": 8.0},
    {"id": "BAT 9.0", "kwh": 9.0},
    {"id": "other", "kwh": None},
]

DEFAULTS: Dict[str, Any] = {
    # SigenStor connection (empty host = not set up yet → configure in Settings)
    "host": "",
    "port": 502,
    "plant_unit": 247,
    "inverter_units": [1],
    # Plant description (edited in Settings → Plant)
    "battery": {"model": "", "modules": None, "module_kwh": None, "cycle_life": 3500},
    "location": {"lat": None, "lon": None, "name": ""},
    "grid": {"fuse_a": None, "phases": 3},     # main fuse; drives the grid/home rings
    "pv": {"tilt": 30, "strings": []},          # strings: [{name, panels, watt, compass}]
    "forecast": {"enabled": True, "refresh_minutes": 60},
    # Server
    "poll_seconds": 5,
    "store_seconds": 30,
    "http_host": "0.0.0.0",
    "http_port": 8742,
    "db_path": "sunbird.db",
    "retention_days": 730,
    "demo": False,
}
PLANT_KEYS = ("host", "port", "plant_unit", "inverter_units", "battery", "grid", "location", "pv", "forecast")


def _migrate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Accept older config.json layouts (forecast.planes / battery_cycle_life)."""
    fc = cfg.get("forecast") or {}
    if fc.get("planes") and not (cfg.get("pv") or {}).get("strings"):
        strings = []
        for p in fc["planes"]:
            s = {"name": p.get("name", f"PV{len(strings) + 1}"), "compass": p.get("compass", 180)}
            m = re.search(r"(\d+)\s*[x×]\s*.*?(\d{3})\s*W", p.get("note", ""))
            if m:
                s["panels"], s["watt"] = int(m.group(1)), int(m.group(2))
            else:
                s["kwp"] = p.get("kwp")
            strings.append(s)
        cfg["pv"] = {"tilt": fc["planes"][0].get("tilt", 30), "strings": strings}
    if fc.get("lat") is not None and not (cfg.get("location") or {}).get("lat"):
        cfg["location"] = {"lat": fc.get("lat"), "lon": fc.get("lon"), "name": ""}
    cfg["forecast"] = {"enabled": fc.get("enabled", True), "refresh_minutes": fc.get("refresh_minutes", 60)}
    if "battery_cycle_life" in cfg:
        cfg.setdefault("battery", {})
        cfg["battery"].setdefault("cycle_life", cfg.pop("battery_cycle_life"))
    return cfg


def load_config() -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        user = _migrate(json.loads(CONFIG_PATH.read_text()))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    for k, v in DEFAULTS.items():  # SUNBIRD_HOST=… etc. override simple values
        env = os.environ.get("SUNBIRD_" + k.upper())
        if env is None or isinstance(v, dict):
            continue
        if isinstance(v, bool):
            cfg[k] = env.lower() in ("1", "true", "yes")
        elif isinstance(v, int):
            cfg[k] = int(env)
        elif isinstance(v, list):
            cfg[k] = [int(x) for x in env.split(",") if x.strip()]
        else:
            cfg[k] = env
    return cfg


def save_config(cfg: Dict[str, Any]):
    """Write config.json atomically, keeping server keys the user may have set by hand."""
    existing = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    existing = _migrate(existing)
    for k in PLANT_KEYS:   # server keys (port, db, …) stay as the user wrote them, or default
        existing[k] = cfg[k]
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, CONFIG_PATH)


def string_kwp(s: Dict[str, Any]) -> Optional[float]:
    if s.get("panels") and s.get("watt"):
        return round(float(s["panels"]) * float(s["watt"]) / 1000, 3)
    return float(s["kwp"]) if s.get("kwp") else None


def grid_max_kw(cfg: Dict[str, Any]) -> Optional[float]:
    g = cfg.get("grid") or {}
    if not g.get("fuse_a"):
        return None
    return round(float(g["fuse_a"]) * 230 * (g.get("phases") or 3) / 1000, 2)


def forecast_planes(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    pv = cfg.get("pv") or {}
    planes = []
    for s in pv.get("strings") or []:
        kwp = string_kwp(s)
        if kwp:
            planes.append({"name": s.get("name") or f"PV{len(planes) + 1}", "compass": float(s.get("compass", 180)),
                           "tilt": float(s.get("tilt", pv.get("tilt", 30))), "kwp": kwp})
    return planes


def validate_plant(body: Dict[str, Any]) -> Dict[str, Any]:
    """Clean up what the Settings form sends. Raises ValueError with a readable message."""
    out: Dict[str, Any] = {}
    host = str(body.get("host", "")).strip()
    if host and not re.fullmatch(r"[A-Za-z0-9.\-:]{1,253}", host):
        raise ValueError("The IP address / host name doesn't look right")
    out["host"] = host
    out["port"] = int(body.get("port") or 502)
    out["plant_unit"] = int(body.get("plant_unit") or 247)
    units = body.get("inverter_units") or [1]
    if isinstance(units, str):
        units = [u for u in re.split(r"[,\s]+", units) if u]
    out["inverter_units"] = [int(u) for u in units][:8] or [1]
    b = body.get("battery") or {}
    out["battery"] = {
        "model": str(b.get("model") or ""),
        "modules": int(b["modules"]) if b.get("modules") not in (None, "") else None,
        "module_kwh": float(b["module_kwh"]) if b.get("module_kwh") not in (None, "") else None,
        "cycle_life": int(b.get("cycle_life") or 3500),
    }
    known = next((m["kwh"] for m in BATTERY_MODELS if m["id"] == out["battery"]["model"] and m["kwh"]), None)
    if known:
        out["battery"]["module_kwh"] = known
    g = body.get("grid") or {}
    fuse = float(g["fuse_a"]) if g.get("fuse_a") not in (None, "") else None
    if fuse is not None and not (1 <= fuse <= 1000):
        raise ValueError("Main fuse should be between 1 and 1000 A")
    if fuse is not None and fuse.is_integer():
        fuse = int(fuse)
    out["grid"] = {"fuse_a": fuse, "phases": 1 if int(g.get("phases") or 3) == 1 else 3}
    loc = body.get("location") or {}
    lat = loc.get("lat"); lon = loc.get("lon")
    lat = float(lat) if lat not in (None, "") else None
    lon = float(lon) if lon not in (None, "") else None
    if (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)):
        raise ValueError("Location needs both a valid latitude and longitude")
    out["location"] = {"lat": lat, "lon": lon, "name": str(loc.get("name") or "")[:80]}
    pv = body.get("pv") or {}
    strings = []
    for i, s in enumerate((pv.get("strings") or [])[:4]):
        panels = int(s["panels"]) if s.get("panels") not in (None, "") else None
        watt = float(s["watt"]) if s.get("watt") not in (None, "") else None
        compass = float(s.get("compass") if s.get("compass") not in (None, "") else 180) % 360
        if not panels and not watt:
            continue
        strings.append({"name": f"PV{i + 1}", "panels": panels, "watt": watt, "compass": compass})
    out["pv"] = {"tilt": float(pv.get("tilt") if pv.get("tilt") not in (None, "") else 30), "strings": strings}
    fc = body.get("forecast") or {}
    out["forecast"] = {"enabled": bool(fc.get("enabled", True)), "refresh_minutes": 60}
    return out


class State:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.snapshot: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.last_ok: Optional[float] = None
        self.listeners: Set[asyncio.Queue] = set()
        self.fc_task: Optional[asyncio.Task] = None
        db = cfg["db_path"]
        if not os.path.isabs(db):
            db = str(ROOT / db)
        self.store = Store(db, cfg["retention_days"], cfg["store_seconds"])
        from .prices import Prices
        self.prices = Prices(self.store, demo=cfg["demo"])
        self.forecaster = None
        self.source = None
        if cfg["demo"]:
            self._demo_defaults()
            from .simulator import DemoSource
            self.source = DemoSource()
            last = self.store.last_row()
            if last:  # resume the simulated counters where the saved history ends
                m = self.source.model
                for k, col in (("pv", "pv_total"), ("load", "load_total"), ("imp", "imp_total"),
                               ("exp", "exp_total"), ("chg", "chg_total"), ("dis", "dis_total")):
                    if last.get(col) is not None:
                        m.t[k] = last[col]
                if last.get("soc") is not None:
                    m.soc = last["soc"]
            if self.store.is_empty():
                log.info("Demo: seeding 60 days of simulated history...")
                start = time.time() - 60 * 86400
                for ts, raw in self.source.backfill(start, cfg["store_seconds"]):
                    self.store.add(build_snapshot(raw), ts=ts, commit=False)
                self.store.commit()
        else:
            self._make_source()
        self._make_forecaster()

    def _demo_defaults(self):
        c = self.cfg
        if not c["pv"].get("strings"):
            c["pv"] = {"tilt": 27, "strings": [{"name": "PV1", "panels": 8, "watt": 470, "compass": 143},
                                               {"name": "PV2", "panels": 16, "watt": 470, "compass": 233}]}
        if c["location"].get("lat") is None:
            c["location"] = {"lat": 59.33, "lon": 18.07, "name": "Stockholm"}
        if not c["battery"].get("modules"):
            c["battery"] = {**c["battery"], "model": "BAT 8.0", "modules": 2, "module_kwh": 8.0}

    def _make_source(self):
        if self.cfg["demo"]:
            return
        if not self.cfg.get("host"):
            self.source = None
            return
        from .modbus_client import SigenModbus
        self.source = SigenModbus(self.cfg["host"], self.cfg["port"], self.cfg["plant_unit"],
                                  self.cfg["inverter_units"])

    def _make_forecaster(self):
        if self.fc_task:
            self.fc_task.cancel()
            self.fc_task = None
        self.forecaster = None
        loc, planes = self.cfg["location"], forecast_planes(self.cfg)
        if self.cfg["forecast"].get("enabled") and planes and loc.get("lat") is not None:
            from .forecast import Forecaster, compass_to_api
            for p in planes:
                p["azimuth"] = compass_to_api(p["compass"])
            fc = {"lat": round(loc["lat"], 2), "lon": round(loc["lon"], 2),
                  "refresh_minutes": self.cfg["forecast"].get("refresh_minutes", 60), "planes": planes}
            self.forecaster = Forecaster(fc, self.store, demo=self.cfg["demo"])

    def start_forecaster(self):
        if self.forecaster:
            self.fc_task = asyncio.create_task(self.forecaster.run())

    async def apply_plant(self, plant: Dict[str, Any]):
        """Apply new plant settings live: reconnect Modbus and restart the forecast if needed."""
        old = {k: self.cfg.get(k) for k in ("host", "port", "plant_unit", "inverter_units")}
        self.cfg.update(plant)
        if not self.cfg["demo"]:
            save_config(self.cfg)
        new = {k: self.cfg.get(k) for k in old}
        if new != old and not self.cfg["demo"]:
            if self.source:
                await self.source.close()
            self.snapshot, self.error, self.last_ok = None, None, None
            self._make_source()
        self._make_forecaster()
        self.start_forecaster()

    def plant_public(self) -> Dict[str, Any]:
        p = {k: copy.deepcopy(self.cfg[k]) for k in PLANT_KEYS}
        for s in p["pv"]["strings"]:
            s["kwp"] = string_kwp(s)
        return p

    def status(self) -> Dict[str, Any]:
        configured = bool(self.cfg["demo"] or self.cfg.get("host"))
        return {
            "source": "demo" if self.cfg["demo"] else (f"{self.cfg['host']}:{self.cfg['port']}" if configured else ""),
            "configured": configured,
            "connected": configured and self.error is None and self.last_ok is not None,
            "error": self.error if configured else None,
            "last_ok": self.last_ok,
            "poll_seconds": self.cfg["poll_seconds"],
        }

    def payload(self) -> Dict[str, Any]:
        b = self.cfg["battery"]
        return {"status": self.status(), "snapshot": self.snapshot, "today": self.today(),
                "battery_cycle_life": b.get("cycle_life", 3500),
                "battery": {"model": b.get("model"), "modules": b.get("modules"), "module_kwh": b.get("module_kwh")},
                "grid_fuse_a": (self.cfg.get("grid") or {}).get("fuse_a"),
                "grid_max_kw": grid_max_kw(self.cfg),
                "pv_kwp": round(sum(string_kwp(x) or 0 for x in (self.cfg.get("pv") or {}).get("strings") or []), 3) or None}

    def today(self) -> Dict[str, Any]:
        d = self.store.daily(1)[0]
        dev = (self.snapshot or {}).get("device_today", {})
        # Sunbird's own counter deltas first (survive device reboots); device day counters as fallback
        for k in ("pv", "load", "charge", "discharge"):
            if d.get(k) is None and dev.get(k) is not None:
                d[k] = dev[k]
        pv, load = d.get("pv") or 0, d.get("load") or 0
        imp, exp = d.get("import") or 0, d.get("export") or 0
        # share of home use covered by solar + battery, worked out hour by hour, so grid energy that
        # charged the battery (e.g. cheap night prices) doesn't push the number to 0 %
        e = (self.store.energy("day", 1) or [{}])[-1]
        if e.get("src_grid") is not None and (e.get("load") or 0) > 0.05:
            d["self_sufficiency"] = max(0.0, min(100.0, (1 - e["src_grid"] / e["load"]) * 100))
            d["grid_home"] = e["src_grid"]
        else:
            d["self_sufficiency"] = max(0.0, min(100.0, (1 - imp / load) * 100)) if load > 0.05 else None
        d["self_consumption"] = max(0.0, min(100.0, (1 - exp / pv) * 100)) if pv > 0.05 else None
        d["pv_yesterday"] = dev.get("pv_yesterday")
        # today's highest values, for the peak marks on the "Right now" rings
        midnight = datetime.combine(date.today(), datetime.min.time()).timestamp()
        pk = self.store.peaks(midnight)
        snap = self.snapshot or {}
        now = {"pv": (snap.get("power") or {}).get("pv"), "load": (snap.get("power") or {}).get("load"),
               "grid": abs((snap.get("power") or {}).get("grid") or 0), "soc": (snap.get("battery") or {}).get("soc")}
        # keep a running maximum for the day as well, so a short spike between two saved samples counts
        # and the mark never moves back down until midnight
        day = date.today().isoformat()
        if getattr(self, "_pk_day", None) != day:
            self._pk_day, self._pk = day, {}
        for k in pk:
            vals = [v for v in (pk.get(k), now.get(k), self._pk.get(k)) if v is not None]
            self._pk[k] = max(vals) if vals else None
        d["peaks"] = dict(self._pk)
        return d

    async def broadcast(self):
        msg = json.dumps(self.payload())
        for q in list(self.listeners):
            if q.qsize() < 5:
                q.put_nowait(msg)

    async def run(self):
        last_store = 0.0
        last_prune = 0.0
        backoff = self.cfg["poll_seconds"]
        while True:
            t0 = time.time()
            src = self.source
            if src is None:  # not set up yet - wait for Settings
                await self.broadcast()
                await asyncio.sleep(3)
                continue
            try:
                raw = await asyncio.wait_for(src.poll(), timeout=20)
                if src is not self.source:  # settings changed while polling
                    continue
                self.snapshot = build_snapshot(raw)
                self.last_ok = time.time()
                if self.error:
                    log.info("Connection restored")
                self.error = None
                backoff = self.cfg["poll_seconds"]
                if t0 - last_store >= self.cfg["store_seconds"]:
                    self.store.add(self.snapshot)
                    last_store = t0
                if t0 - last_prune > 86400:
                    self.store.prune()
                    last_prune = t0
            except Exception as e:  # keep running whatever happens
                if src is not self.source:
                    continue
                msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                if msg != self.error:
                    log.warning("Poll failed: %s", msg)
                self.error = msg
                backoff = min(60, backoff * 2)
            await self.broadcast()
            delay = self.cfg["poll_seconds"] if self.error is None else backoff
            await asyncio.sleep(max(0.5, delay - (time.time() - t0)))


async def test_connection(host: str, port: int, plant_unit: int) -> Dict[str, Any]:
    """Quick check used by the Settings form: port open? plant answers? which inverter IDs?"""
    import socket
    from .modbus_client import SigenModbus
    from .registers import PLANT, by_name, decode
    try:
        await asyncio.wait_for(asyncio.to_thread(socket.create_connection, (host, port), 4), 6)
    except Exception as e:
        return {"ok": False, "step": "port", "message": f"Can't reach {host}:{port} ({type(e).__name__}). "
                "Check the address and that Modbus-TCP is enabled on the SigenStor."}
    c = SigenModbus(host, port, plant_unit, inverter_units=[], timeout=3)
    try:
        regs = by_name(PLANT)
        w = await asyncio.wait_for(c._read(plant_unit, 30014, 1), 6)
        if w is None:
            return {"ok": False, "step": "plant", "message": f"Port is open but device ID {plant_unit} didn't answer."}
        soc = decode(regs["ess_soc"], w)
        found = []
        for unit in range(1, 5):
            try:
                if await asyncio.wait_for(c._read(unit, 30578, 1), 4) is not None:
                    found.append(unit)
            except Exception:
                pass
        return {"ok": True, "soc": soc, "inverter_units": found,
                "message": f"Connected · battery {soc:.0f} %" + (f" · inverter ID {', '.join(map(str, found))}" if found else "")}
    except Exception as e:
        return {"ok": False, "step": "plant", "message": f"Connected to the port but reading failed: {e}"}
    finally:
        await c.close()


def create_app(cfg: Optional[Dict[str, Any]] = None) -> FastAPI:
    cfg = cfg or load_config()
    state = State(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(state.run())
        state.start_forecaster()
        yield
        task.cancel()
        if state.fc_task:
            state.fc_task.cancel()
        if state.source:
            await state.source.close()

    app = FastAPI(title="Sunbird", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(WEB / "favicon.ico")

    @app.get("/api/live")
    def live():
        return JSONResponse(state.payload())

    # ---- settings ---------------------------------------------------------
    @app.get("/api/config")
    def get_config():
        return JSONResponse({"plant": state.plant_public(), "demo": state.cfg["demo"],
                             "battery_models": BATTERY_MODELS})

    @app.post("/api/config")
    async def post_config(body: Dict[str, Any] = Body(...)):
        try:
            plant = validate_plant(body)
        except (ValueError, TypeError, KeyError) as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
        await state.apply_plant(plant)
        msg = "Applied (demo mode - not written to config.json)" if state.cfg["demo"] else "Saved"
        return JSONResponse({"ok": True, "message": msg, "plant": state.plant_public()})

    @app.post("/api/test-connection")
    async def post_test(body: Dict[str, Any] = Body(...)):
        host = str(body.get("host", "")).strip()
        if not host:
            return JSONResponse({"ok": False, "message": "Enter the SigenStor's IP address first"})
        if state.cfg["demo"]:
            return JSONResponse({"ok": True, "message": "Demo mode - no real connection made"})
        return JSONResponse(await test_connection(host, int(body.get("port") or 502), int(body.get("plant_unit") or 247)))

    # ---- data ---------------------------------------------------------------
    @app.get("/api/history")
    def history(hours: float = Query(24, ge=0.25, le=24 * 400)):
        return JSONResponse(state.store.history(hours))

    @app.get("/api/daily")
    def daily(days: int = Query(14, ge=1, le=400)):
        rows = state.store.daily(days)
        rows[-1] = {**rows[-1], **{k: v for k, v in state.today().items() if k in rows[-1]}}
        return JSONResponse(rows)

    @app.get("/api/energy")
    def energy(group: str = Query("day", pattern="^(hour|day|week|month)$"),
               n: int = Query(30, ge=0, le=20000)):
        return JSONResponse(state.store.energy(group, n))

    @app.get("/api/profile")
    def profile(days: int = Query(30, ge=1, le=400)):
        return JSONResponse(state.store.profile(days))

    @app.get("/api/prices")
    async def prices(zone: str = Query("SE3", pattern="^SE[1-4]$")):
        """Spot prices (SEK/kWh excl. moms) for yesterday, today and tomorrow (once published)."""
        from datetime import date, timedelta
        today = date.today()
        days = {}
        for k, d in (("yesterday", today - timedelta(days=1)), ("today", today), ("tomorrow", today + timedelta(days=1))):
            days[k] = await asyncio.to_thread(state.prices.day, zone, d)
        return JSONResponse({"zone": zone, "source": "demo" if state.cfg["demo"] else "elprisetjustnu.se (Nord Pool)",
                             "error": state.prices.error, **days})

    @app.get("/api/economy")
    async def economy(zone: str = Query("SE3", pattern="^SE[1-4]$"), days: int = Query(30, ge=1, le=400)):
        """Hourly kWh (bought, sold, home, solar, battery) with that hour's spot price, for working out costs.
        Fees and moms are added in the browser from the user's price settings."""
        from datetime import date as _date, datetime as _dt
        rows = [r for r in state.store.energy("hour", days * 24) if r.get("load") is not None]
        if not rows:
            return JSONResponse({"zone": zone, "rows": [], "missing": 0})
        for d in sorted({_dt.fromtimestamp(r["t"]).date() for r in rows}):   # fetches and caches missing days
            if d <= _date.today():
                await asyncio.to_thread(state.prices.day, zone, d)
        start, end = rows[0]["t"], rows[-1]["t"] + 3600
        hourly: Dict[int, List[float]] = {}
        for p in state.store.prices(zone, start, end):
            h = p["t"] - p["t"] % 3600 if p["te"] - p["t"] <= 3600 else p["t"]
            hourly.setdefault(h, []).append(p["sek"])
        out, missing = [], 0
        for r in rows:
            ps = hourly.get(r["t"])
            spot = round(sum(ps) / len(ps) * 100, 3) if ps else None   # öre/kWh excl. moms
            if spot is None and (r.get("import") or r.get("export")):
                missing += 1
            out.append({"t": r["t"], "spot": spot, **{k: r.get(k) for k in
                        ("import", "export", "load", "pv", "charge", "discharge", "src_solar", "src_battery", "src_grid")}})
        return JSONResponse({"zone": zone, "rows": out, "missing": missing, "error": state.prices.error})

    @app.get("/api/forecast")
    def forecast(past_days: int = Query(14, ge=0, le=90)):
        """Forecast curve for today + tomorrow, and forecast vs actual per day."""
        if not state.forecaster:
            return JSONResponse({"status": {"enabled": False}})
        from datetime import datetime, timedelta
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = today - timedelta(days=1), today + timedelta(days=2)
        points = state.store.forecast_points(start.timestamp(), end.timestamp())
        first = (today - timedelta(days=past_days)).strftime("%Y-%m-%d")
        fdays = state.store.forecast_days(first)
        actual = {datetime.fromtimestamp(r["t"]).strftime("%Y-%m-%d"): r
                  for r in state.store.energy("day", past_days + 1)}
        names = [p["name"] for p in state.forecaster.planes]
        days = []
        for i in range(-past_days, 2):
            d = (today + timedelta(days=i)).strftime("%Y-%m-%d")
            f, a = fdays.get(d, {}), actual.get(d, {})
            row = {"date": d, "t": int((today + timedelta(days=i)).timestamp()),
                   "forecast": f.get("total"), "actual": a.get("pv") if i <= 0 else None}
            for n, plane in enumerate(names, start=1):
                row[f"f_{plane}"] = f.get(plane)
                row[f"a_{plane}"] = a.get(f"s{n}") if i <= 0 else None
            days.append(row)
        return JSONResponse({"status": state.forecaster.status(), "points": points, "days": days})

    @app.get("/api/raw")
    async def raw():
        """Debug: every register value as read."""
        if not state.source:
            return JSONResponse({"error": "not configured"}, status_code=409)
        try:
            return JSONResponse(json.loads(json.dumps(await state.source.poll(), default=str)))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=502)

    @app.get("/api/stream")
    async def stream():
        q: asyncio.Queue = asyncio.Queue()
        state.listeners.add(q)

        async def gen():
            try:
                yield f"data: {json.dumps(state.payload())}\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=25)
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                state.listeners.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
