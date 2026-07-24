from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="MOMO Camera Hub")
    parser.add_argument("--config", type=Path, help="optional YAML configuration")
    parser.add_argument("--host", help="override HTTP bind host")
    parser.add_argument("--port", type=int, help="override HTTP port")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port, log_level="info")


if __name__ == "__main__":
    main()
