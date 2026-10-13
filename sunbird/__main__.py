"""python -m sunbird [--demo] [--host IP] [--port 8742]"""
from __future__ import annotations

import argparse
import logging
import socket

import uvicorn

from .app import create_app, load_config


def main():
    ap = argparse.ArgumentParser(prog="sunbird", description="SigenStor web dashboard")
    ap.add_argument("--demo", action="store_true", help="run with a simulated SigenStor")
    ap.add_argument("--host", help="SigenStor IP address (default from config.json)")
    ap.add_argument("--http-port", type=int, help="web port (default 8742)")
    ap.add_argument("--db", help="SQLite file path")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if args.demo:
        cfg["demo"] = True
        if cfg["db_path"] == "sunbird.db":
            cfg["db_path"] = "sunbird-demo.db"
    if args.host:
        cfg["host"] = args.host
    if args.http_port:
        cfg["http_port"] = args.http_port
    if args.db:
        cfg["db_path"] = args.db

    try:  # finds the outward-facing LAN address; no packets are sent
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            lan_ip = s.getsockname()[0]
    except OSError:
        lan_ip = "<this-computer's-ip>"
    src = ("simulated SigenStor" if cfg["demo"] else f"SigenStor at {cfg['host']}:{cfg['port']}" if cfg.get("host")
           else "no SigenStor set up yet - open the dashboard and go to Settings")
    print(f"\n  Sunbird → {src}\n  Dashboard: http://localhost:{cfg['http_port']}"
          f"  (on your network: http://{lan_ip}:{cfg['http_port']})\n")
    uvicorn.run(create_app(cfg), host=cfg["http_host"], port=cfg["http_port"], log_level="warning")


if __name__ == "__main__":
    main()
