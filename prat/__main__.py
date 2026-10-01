"""Run with: python -m prat [--host 127.0.0.1] [--port 8000]"""

import argparse
import logging

import uvicorn

from .server import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="Prat — Norwegian conversation practice")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
