"""Command line entry point.

prat                 start the app (http://127.0.0.1:8000)
prat --open          ...and open it in the browser
prat setup           download all models (≈5 GB, once)
"""

import argparse
import logging
import os
import threading
import webbrowser


def main() -> None:
    ap = argparse.ArgumentParser(prog="prat", description="Prat — a local Norwegian conversation partner")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("setup", help="download all models (once, ≈5 GB)")
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    ap.add_argument("--open", action="store_true", help="open the app in the browser once it is running")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.cmd == "setup":
        from .models import download_all

        download_all()
        return

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    from .models import all_cached, go_offline

    if all_cached():
        go_offline()  # everything is on disk: never touch the network
    else:
        print("  Some models are missing; they will download on first use (or run: prat setup).")

    import uvicorn

    from .server import create_app

    url = f"http://{args.host}:{args.port}"
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"\n  Prat is starting at {url}  (Ctrl+C to stop)\n", flush=True)
    uvicorn.run(create_app(preload=True), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
