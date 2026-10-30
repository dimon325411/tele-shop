"""One request: how does OLX mark free ("Za darmo") offers? Checks app/domain/freebies.py on real data.

    python3 scripts/olx_probe_free.py

Prints the raw price param of the newest "oddam" offers and whether the bot treats them as free,
plus the product query it would use to price them. One request only; on 403/429 stop.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.freebies import is_free_junk, is_free_offer, value_query  # noqa: E402
from app.domain.models import SearchCriteria  # noqa: E402
from app.sources.http import USER_AGENT  # noqa: E402
from app.sources.olx import build_url  # noqa: E402


def main() -> int:
    url = build_url("oddam", SearchCriteria(keywords=["oddam"]), limit=40)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: OLX refused. Stop, do not retry in a loop.")
        return 2
    items = data.get("data") or []
    free = 0
    for it in items[:25]:
        price = next((p.get("value") for p in it.get("params") or [] if p.get("key") == "price"), None)
        is_free = is_free_offer(it)
        free += is_free
        flag = "FREE" if is_free else "paid"
        junk = " (skip: junk)" if is_free and is_free_junk(it.get("title")) else ""
        print(f"{flag:4} {str(it.get('title'))[:45]:45} price={json.dumps(price, ensure_ascii=False)[:90]}"
              f" -> query: {value_query(it.get('title'))}{junk}")
    print(f"\n{len(items)} offers, {free} of the first 25 recognised as free. Send this output to Claude.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
