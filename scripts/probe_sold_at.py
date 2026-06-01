"""One-shot probe: fetch one page from the Bandcamp fancollection API and print all
field names present in the first item, plus the value of any date-like fields.

Run from the repo root:
    poetry run python scripts/probe_sold_at.py
"""

import json
import time
from pathlib import Path

import requests

from kamp_core.library import LibraryIndex

DB_PATH = Path("~/.local/share/kamp/library.db").expanduser()
COLLECTION_URL = "https://bandcamp.com/api/fancollection/1/collection_items"


def main() -> None:
    index = LibraryIndex(DB_PATH)
    session_data = index.get_session("bandcamp")
    if not session_data:
        raise SystemExit("No Bandcamp session found — log in via the kamp app first.")

    cookies_list = session_data.get("cookies", [])
    cookies = {
        c["name"]: c["value"]
        for c in cookies_list
        if c.get("domain", "") == "bandcamp.com"
        or c.get("domain", "").endswith(".bandcamp.com")
    }

    fan_url = "https://bandcamp.com/api/fan/2/collection_summary"
    r = requests.get(fan_url, cookies=cookies, timeout=10)
    r.raise_for_status()
    fan_id = r.json()["fan_id"]
    print(f"fan_id: {fan_id}")

    payload = {
        "fan_id": fan_id,
        "count": 2,
        "older_than_token": f"{int(time.time())}:0:a::",
    }
    r2 = requests.post(COLLECTION_URL, json=payload, cookies=cookies, timeout=30)
    r2.raise_for_status()
    result = r2.json()

    items = result.get("items", [])
    if not items:
        print("No items returned.")
        return

    item = items[0]
    print(f"\nAll fields in first item ({len(items)} returned):")
    for k, v in sorted(item.items()):
        print(f"  {k!r}: {v!r}")

    date_fields = {
        k: v
        for k, v in item.items()
        if any(
            w in k.lower()
            for w in ("date", "sold", "time", "added", "stamp", "purchased")
        )
    }
    print(f"\nDate-like fields: {json.dumps(date_fields, indent=2)}")

    print(f"\nlast_token from response: {result.get('last_token', '(none)')!r}")


if __name__ == "__main__":
    main()
