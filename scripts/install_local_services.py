"""Install launchd user agents for the local PostgreSQL, bot and daily backup."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = Path.home() / "Library" / "LaunchAgents"
DOMAIN = f"gui/{os.getuid()}"


def install(label: str, arguments: list[str], *, calendar: dict | None = None, keep_alive: bool = False) -> None:
    agent = {
        "Label": label,
        "ProgramArguments": arguments,
        "WorkingDirectory": str(ROOT),
        "StandardOutPath": str(ROOT / "data" / f"{label}.out.log"),
        "StandardErrorPath": str(ROOT / "data" / f"{label}.err.log"),
    }
    if calendar:
        agent["StartCalendarInterval"] = calendar
    else:
        agent["RunAtLoad"] = True
        agent["KeepAlive"] = keep_alive
    AGENTS.mkdir(parents=True, exist_ok=True)
    path = AGENTS / f"{label}.plist"
    subprocess.run(["launchctl", "bootout", DOMAIN, str(path)], capture_output=True)
    path.write_bytes(plistlib.dumps(agent))
    subprocess.run(["launchctl", "bootstrap", DOMAIN, str(path)], check=True)


if __name__ == "__main__":
    if "--report-only" not in sys.argv:
        install("pl.telegrammarket.postgres", [
            str(ROOT / "data/pg-runtime/bin/postgres"), "-D", str(ROOT / "data/postgres"),
            "-c", "listen_addresses=", "-c", f"unix_socket_directories={ROOT / 'data/pg-socket'}",
            "-c", "unix_socket_permissions=0700",
        ], keep_alive=True)
        install("pl.telegrammarket.bot", [str(ROOT / ".venv/bin/python"), "-m", "app.dev_run"], keep_alive=True)
        install("pl.telegrammarket.backup", [str(ROOT / "scripts/backup_postgres.sh")],
                calendar={"Hour": 3, "Minute": 15})
    install("pl.telegrammarket.report", [str(ROOT / ".venv/bin/python"),
                                         str(ROOT / "scripts/export_analytics.py"), str(ROOT / "data/reports")],
            calendar={"Hour": 3, "Minute": 30})
    install("pl.telegrammarket.sheets", [str(ROOT / ".venv/bin/python"),
                                         str(ROOT / "scripts/sync_google_sheet.py")],
            calendar={"Hour": 3, "Minute": 45})
