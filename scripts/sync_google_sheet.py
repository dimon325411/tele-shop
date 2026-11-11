"""Refresh the two analytics tabs using a shared Google service account.

Set GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_FILE in .env, then share the Sheet
with the service account email from its JSON. Without credentials, exits cleanly.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import get_settings  # noqa: E402
from scripts.export_analytics import export  # noqa: E402

TABS = {"snapshots": "Statystyki rynku", "price_changes": "Zmiany cen"}


def request(token: str, sheet_id: str, suffix: str, payload: dict) -> None:
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(sheet_id, safe='')}/values{suffix}"
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = Request(url, data=body, headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Google Sheets returned HTTP {response.status}")


def main() -> None:
    settings = get_settings()
    if not settings.google_sheet_id or not settings.google_credentials_file:
        print("Google Sheets sync skipped: configure GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_FILE")
        return
    credentials_file = Path(settings.google_credentials_file).expanduser()
    if not credentials_file.is_file():
        raise FileNotFoundError(credentials_file)
    from google.auth.transport.requests import Request as AuthRequest
    from google.oauth2.service_account import Credentials

    credentials = Credentials.from_service_account_file(
        str(credentials_file), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    credentials.refresh(AuthRequest())
    report_dir = ROOT / "data/reports"
    export(report_dir, settings.database_url)
    data = []
    for name, tab in TABS.items():
        with (report_dir / f"{name}.csv").open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            data.append({"range": f"'{tab}'!A1", "majorDimension": "ROWS", "values": list(reader)})
    request(credentials.token, settings.google_sheet_id, ":batchClear", {
        "ranges": [f"'{tab}'!A:Z" for tab in TABS.values()]})
    request(credentials.token, settings.google_sheet_id, ":batchUpdate", {
        "valueInputOption": "USER_ENTERED", "data": data})
    print("Google Sheets analytics refreshed")


if __name__ == "__main__":
    main()
