"""One-shot check of OLX access for PERSONAL MODE. Needs only Python (no packages).

    python3 scripts/olx_probe.py rower

Makes exactly ONE request to the public offers endpoint allowed by OLX robots.txt, reports the
HTTP status, whether our parser understands the answer, prints the newest listings and saves the
raw answer to tests/fixtures/olx_live_sample.json (git-ignored) so the parser can be tested on it.
If OLX answers 403/429 or with a CAPTCHA page, do NOT retry in a loop — personal mode would pause too.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import SearchCriteria  # noqa: E402
from app.sources.http import USER_AGENT  # noqa: E402
from app.sources.olx import build_url, parse_offers  # noqa: E402


def main(query: str) -> int:
    url = build_url(query, SearchCriteria(keywords=[query]), limit=10)
    print(f"GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               "Accept-Language": "pl"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status, body = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: OLX refused the request. Personal mode would pause this source. Do not retry in a loop.")
        return 2
    except OSError as exc:
        print(f"network error: {exc}")
        return 3
    out = ROOT / "tests" / "fixtures" / "olx_live_sample.json"
    out.write_text(body, encoding="utf-8")
    print(f"HTTP {status}, {len(body)} bytes, saved to {out.relative_to(ROOT)}")
    try:
        listings = parse_offers(body)
    except Exception as exc:  # report, do not hide
        print(f"PARSER PROBLEM: {exc}. Send the saved file to the developer.")
        return 1
    print(f"parsed {len(listings)} listings:")
    for l in listings[:10]:
        price = f"{l.price} {l.currency}" if l.price is not None else "brak ceny"
        posted = l.posted_at.isoformat() if l.posted_at else "BRAK DATY"
        print(f"  - {posted} | {l.title} | {price} | {l.city or '-'} | {l.condition or '-'} | {l.url}")
    dated = sum(1 for l in listings if l.posted_at)
    print(f"listings with publication date: {dated}/{len(listings)} (needed to tell new listings from old ones)")
    if not listings:
        print("no listings parsed (empty result or unknown structure) — check the saved file")
    return 0


if __name__ == "__main__":
    sys.exit(main(" ".join(sys.argv[1:]) or "rower"))
