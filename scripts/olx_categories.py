"""Discover OLX category ids for the reseller presets (run once on your computer, Python only).

    python3 scripts/olx_categories.py

For every preset it sends 2 queries (10 requests total, 3 s apart) to the public offers endpoint,
counts the category ids of the organic results and keeps the dominant ones. Then it checks that
filtering by ``category_id`` works. Result: app/data/olx_categories.json.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.sources.http import USER_AGENT  # noqa: E402
from app.sources.olx_categories import DATA_FILE, PRESETS  # noqa: E402

BASE = "https://www.olx.pl/api/v1/offers/"


def get(params: dict) -> dict:
    req = urllib.request.Request(f"{BASE}?{urlencode(params)}",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Only listings that ARE the product count when choosing a section (a "playstation 5" query returns
# mostly games and collector's editions, which live in other OLX sections).
LOOKS_LIKE = {
    "iphone": (("iphone",), ("etui", "case", "szklo", "folia")),
    "laptopy": (("laptop", "macbook", "notebook"), ()),
    "komputery": (("komputer", "pc", "zestaw"), ("laptop",)),
    "konsole": (("konsola", "ps5", "playstation", "xbox"), ("gra", "gry", "gier", "edycja", "kolekcjonerska",
                                                            "steelbook", "pad", "kontroler")),
    "karty_graficzne": (("karta", "rtx", "gtx", "radeon"), ("laptop", "komputer", "zestaw", "pc")),
}


def looks_like(code: str, title: str) -> bool:
    words = title.lower().replace("-", " ").split()
    if code == "konsole" and any(w.startswith("konsol") for w in words):
        return True  # "Konsola PS5 + 2 pady + gra" is a console
    need, forbidden = LOOKS_LIKE.get(code, ((), ()))
    return (not need or any(w.startswith(n) for w in words for n in need)) and \
        not any(w.startswith(f) for w in words for f in forbidden)


def organic(data: dict) -> list[dict]:
    promoted = set(((data.get("metadata") or {}).get("source") or {}).get("promoted") or [])
    return [it for i, it in enumerate(data.get("data") or []) if i not in promoted]


def main() -> int:
    result: dict[str, list[int]] = {}
    shares: dict[str, dict[int, float]] = {}
    for code, queries in PRESETS.items():
        counts: Counter = Counter()
        for q in queries:
            try:
                data = get({"offset": 0, "limit": 40, "query": q})
            except urllib.error.HTTPError as exc:
                print(f"HTTP {exc.code} for '{q}' — OLX refused; stop and try later. Do not loop.")
                return 2
            for it in organic(data):
                if not looks_like(code, it.get("title") or ""):
                    continue
                cid = (it.get("category") or {}).get("id")
                if cid is not None:
                    counts[cid] += 1
            time.sleep(3)
        total = sum(counts.values()) or 1
        shares[code] = {cid: n / total for cid, n in counts.items()}
        print(f"{code:16} -> {[c for c, n in counts.most_common() if n / total >= 0.2][:3]}   "
              f"(all: {counts.most_common(5)})")
    # a category belongs to the preset where it has the biggest share
    # (e.g. "rtx 3070" also finds whole PCs, but that section belongs to "komputery")
    for code, cats in shares.items():
        result[code] = [cid for cid, share in sorted(cats.items(), key=lambda kv: -kv[1])
                        if share >= 0.2 and all(share >= other.get(cid, 0) for other in shares.values())][:3]
    print("final:", result)
    # verify the filter works for one preset
    some = next((ids[0] for ids in result.values() if ids), None)
    if some is not None:
        data = get({"offset": 0, "limit": 10, "query": "", "category_id": some})
        cats = {(it.get("category") or {}).get("id") for it in organic(data)}
        print(f"category_id filter check ({some}): returned categories {cats}")
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"saved {DATA_FILE.relative_to(ROOT)} — restart the bot to use it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
