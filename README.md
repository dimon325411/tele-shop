# OLX Deal Bot — Telegram marketplace monitor

[🇬🇧 English](#-english) · [🇵🇱 Polski](#-polski)

---

## 🇬🇧 English

A Telegram bot that monitors second-hand listings on **OLX.pl**, compares every new offer with the
market median for the same configuration, and notifies you only about real deals.

> **Personal / educational project.** It runs in *personal mode* for the owner only, with low request
> rates and no circumvention of any protection. It is **not** a commercial service — see
> [Data sources & legal notes](#data-sources--legal-notes).

### What it does

- **Saved searches** created step by step with inline buttons (no JSON, no commands to remember):
  keywords, excluded words, price range, condition, city + radius, check frequency, quiet hours.
- **Deal detection** — each listing is compared with the median of similar listings
  (same model, storage size and condition; broken items, accessories, "kupię"/"zamienię" filtered out).
  Alerts fire only when the price is N % below the median, the listing is fresh and the seller is not brand-new.
- **AI second opinion (optional)** — a local LLM (Ollama, e.g. Qwen3) or Claude API reads the description
  of a deal candidate and flags risks (iCloud lock, damage, scam patterns). AI failure never blocks an alert.
- **Statistics & charts** — per-model median, typical range (25–75 %), 7-day trend, price histogram,
  cheapest fresh offers with links.
- **"Free stuff" alerts** — free items nearby whose estimated market value is ≥ 200 zł.
- **Daily digest**, location filter (12 major Polish cities, 10/30/50 km), Polish UI with centralized i18n.

### Tech stack

| Area | Tools |
|---|---|
| Bot | Python 3, **aiogram 3** |
| API & health checks | **FastAPI** |
| Database | **PostgreSQL 16**, SQLAlchemy 2 Core, Alembic, raw SQL migrations |
| Queue / locks / cache | **Redis**, ARQ |
| HTTP & validation | httpx, Pydantic |
| AI | Ollama (local LLM) or Claude API |
| Charts | matplotlib |
| Deployment | **Docker Compose**; Windows scripts with autostart |
| Tests | unittest / pytest, 200+ unit tests |

### Architecture

A modular monolith with separate processes for the bot and the background worker.
Business logic lives in framework-free, fully tested modules (`app/domain`, `app/services`),
and the aiogram / FastAPI / ARQ layers stay thin.

```
app/
├── domain/      # matching, variants (model/GB/condition), area, freebies — pure logic
├── services/    # monitoring, market median, deals, digest, AI advisor, notifications
├── sources/     # one adapter per marketplace + mock source; polite HTTP client
├── storage/     # in-memory store (dev) and PostgreSQL store (SQL)
├── bot/         # aiogram handlers, wizard, keyboards
├── worker/      # scheduled jobs (ARQ)
├── api/         # FastAPI: health checks, webhooks
└── i18n/        # all user-facing texts
migrations/      # SQL migrations 0001–0008
tests/           # unit + integration (Docker)
```

Engineering highlights:
- **Deduplication** — one listing matching several searches is sent once; safe under parallel jobs and retries.
- **Reliability** — Redis locks, `SKIP LOCKED` job claiming, exponential backoff, recovery after crashes.
- **Polite source access** — rate limits, timeouts; on HTTP 403/429/CAPTCHA the source is paused, never bypassed.
- **Payments (prepared, disabled)** — Telegram Stars (`XTR`) flow with idempotent event handling and refunds,
  tested on mock events only.

### Quick start (demo mode, no Docker)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # set BOT_TOKEN and ADMIN_IDS
python -m app.dev_run
```

Full stack with PostgreSQL and Redis:

```bash
docker compose up -d --build
```

Windows: `scripts/windows/setup.bat`, then `run_bot.bat` (see `docs/WINDOWS.md`).

### Tests

```bash
python -m unittest discover -s tests/unit -t .
docker compose --profile tests run --rm tests   # integration tests (PostgreSQL + Redis)
```

### Data sources & legal notes

- **OLX.pl** — personal mode reads the public offers endpoint allowed in `robots.txt`, at most once
  per 10 minutes per search. The OLX terms of service forbid aggregating their data for third parties,
  so a commercial multi-user version would require written permission from OLX.
- **Allegro Lokalnie** — adapter disabled: Allegro rules forbid scraping without consent.
- Analysis with sources: `docs/SOURCES.md`.

No listing data, tokens or personal data are included in this repository.

---

## 🇵🇱 Polski

Bot Telegram, który monitoruje ogłoszenia z drugiej ręki na **OLX.pl**, porównuje każdą nową ofertę
z medianą cen takich samych konfiguracji i powiadamia tylko o prawdziwych okazjach.

> **Projekt osobisty i edukacyjny.** Działa w trybie prywatnym, tylko dla właściciela, z niską
> częstotliwością zapytań i bez obchodzenia jakichkolwiek zabezpieczeń. To **nie** jest usługa komercyjna.

### Funkcje

- **Zapisane wyszukiwania** tworzone krok po kroku przyciskami: słowa kluczowe, wykluczenia, cena,
  stan, miasto i promień, częstotliwość sprawdzania, godziny ciszy.
- **Wykrywanie okazji**: cena porównywana z medianą podobnych ogłoszeń (ten sam model, pamięć, stan;
  bez uszkodzonych, akcesoriów, „kupię” i „zamienię”). Powiadomienie tylko przy cenie N % poniżej mediany,
  dla świeżego ogłoszenia i sprawdzonego sprzedawcy.
- **Opinia AI (opcjonalnie)**: lokalny model (Ollama) lub Claude API czyta opis i ostrzega o ryzyku.
- **Statystyki i wykresy**: mediana, typowy przedział cen, trend 7-dniowy, najtańsze świeże oferty.
- **„Oddam za darmo”**: darmowe rzeczy w okolicy o wartości rynkowej ≥ 200 zł.
- **Dzienne podsumowanie**, filtr lokalizacji, polski interfejs z centralnymi tłumaczeniami.

### Technologie

Python, aiogram 3, FastAPI, PostgreSQL, SQLAlchemy 2, Alembic, Redis, ARQ, httpx, Pydantic,
Ollama / Claude API, Docker Compose. Ponad 200 testów jednostkowych.

### Uruchomienie

Instrukcja jest w angielskiej części powyżej ([Quick start](#quick-start-demo-mode-no-docker)).
Skrót: `pip install -r requirements.txt`, uzupełnij `.env` i uruchom `python -m app.dev_run`.

### Źródła danych

OLX.pl jest czytany w trybie prywatnym, z ograniczoną częstotliwością. Regulamin OLX zabrania agregacji
danych dla osób trzecich, dlatego wersja komercyjna wymagałaby pisemnej zgody OLX. Adapter Allegro
Lokalnie jest wyłączony. Szczegóły: `docs/SOURCES.md`.

# Local PostgreSQL and analytics

The macOS user agents installed by `python scripts/install_local_services.py` start the
private local PostgreSQL socket and bot after login. They create a `pg_dump` backup at
03:15 and refresh aggregate CSV reports in `data/reports/` at 03:30. Data and database
binaries are under ignored `data/`; keep this directory when updating the code.

The daily Google Sheets sync runs at 03:45 once `GOOGLE_SHEET_ID` and
`GOOGLE_CREDENTIALS_FILE` are configured in `.env`. Install `requirements.txt`, create a
Google service account, and share the report spreadsheet with its `client_email` as an
Editor. Place the JSON credential file outside Git. Until then, the sync exits without
changing the existing Sheet. The report contains search-level aggregates only; price
history before the PostgreSQL migration is represented by `baseline` rows, not inferred
historical changes.
