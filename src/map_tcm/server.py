from __future__ import annotations

import argparse
from pathlib import Path
from wsgiref.simple_server import make_server

from .api import MapApplication


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the MAP-TCM Alpha local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--db", default=".local/map_tcm_demo.db", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    application = MapApplication(args.db)
    with make_server(args.host, args.port, application) as server:
        print(f"MAP-TCM Alpha running at http://{args.host}:{args.port}")
        print("Synthetic-data mode only. Press Ctrl+C to stop.")
        server.serve_forever()


if __name__ == "__main__":
    main()
