"""Scan Hugging Face for model releases relevant to Prat since the last scan.

Standard library only, so it runs anywhere: ``python3 .claude/skills/model-scout/scan.py``.

  --since YYYY-MM-DD   override the date in watchlist.json
  --save               record today as the last scan (do this once the report is done)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://huggingface.co/api"
WATCHLIST = Path(__file__).with_name("watchlist.json")


def get(path: str, **params) -> tuple[int, object]:
    url = f"{API}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def recent(models: list[dict], since: str) -> list[dict]:
    return [m for m in models if (m.get("lastModified") or m.get("createdAt") or "")[:10] >= since]


def line(m: dict) -> str:
    tag = m.get("pipeline_tag") or m.get("library_name") or "?"
    return f"- `{m['id']}` ({tag}, {(m.get('lastModified') or '')[:10]}, {m.get('downloads', 0)} downloads)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    watch = json.loads(WATCHLIST.read_text())
    since = args.since or watch["last_scan"]
    print(f"# Hugging Face scan since {since}\n")

    print("## Watched private repos")
    for repo, note in watch["private_watch"].items():
        status, _ = get(f"models/{repo}")
        state = "**NOW PUBLIC**" if status == 200 else f"still private ({status})"
        print(f"- `{repo}`: {state}. {note}")

    seen: set[str] = set()
    print("\n## New or updated models from watched authors")
    for author, keywords in watch["authors"].items():
        _, models = get("models", author=author, sort="lastModified", direction=-1, limit=200, full="true")
        hits = [m for m in recent(models or [], since) if not keywords or any(k in m["id"].lower() for k in keywords)]
        for m in hits:
            seen.add(m["id"])
        if hits:
            print(f"\n### {author}")
            print("\n".join(line(m) for m in hits))

    print("\n## Search hits (any author)")
    for q in watch["searches"]:
        _, models = get("models", search=q, sort="lastModified", direction=-1, limit=50, full="true")
        hits = [m for m in recent(models or [], since) if m["id"] not in seen]
        for m in hits:
            seen.add(m["id"])
        if hits:
            print(f'\n### "{q}"')
            print("\n".join(line(m) for m in hits))

    print("\n## NbAiLab Spaces updated (often the first sign of a new model)")
    _, spaces = get("spaces", author="NbAiLab", sort="lastModified", direction=-1, limit=100)
    for s in recent(spaces or [], since):
        print(f"- `{s['id']}` ({(s.get('lastModified') or '')[:10]})")

    if args.save:
        watch["last_scan"] = dt.date.today().isoformat()
        WATCHLIST.write_text(json.dumps(watch, indent=2, ensure_ascii=False) + "\n")
        print(f"\n(last_scan saved as {watch['last_scan']})")


if __name__ == "__main__":
    main()
