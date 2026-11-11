# Bot logic and reliability implementation plan

Goal: resolve the failures documented in AUDIT-2026-11-10.ru.md and the median review.
Architecture: retain services, memory/SQL repositories and the notification outbox. Add typed
configuration grouping, persisted background-job state and free-item outbox payloads.
Scope approved by owner: “popraw to zeby wszystko dzialalo poprawnie i logicznie”.
Constraints: preserve existing changes/data, Polish copy, UTC, no live payments, no source enablement,
no push, no second live polling. Work on codex/logic-reliability in the existing checkout.

## Tasks (each: failing regression → minimal implementation → focused green suite)

- [x] 1. Comparable medians: domain/variants.py, services/market.py, analytics.py, presentation.py.
  Test GPU 8/16 GB, desktop RAM/SSD, laptop RAM/storage, unknown configuration, description defects,
  card fallback and explanatory footer. All consumer paths must share grouping semantics.
- [x] 2. Monitoring and HTTP: monitoring.py and sources/http.py. Test rejected AI listing after price
  drop, fresh/area/discount filters for price drops, queued concurrent HTTP after 429 and retry.
- [x] 3. Reliable jobs: storage/repo.py, memory.py, sql.py, sql_queries.py; migration 0009.
  Persist JSON job state and notification payload; add free notification kind. Freebies queue per user,
  retry failed delivery through NotificationService, respect quiet hours/access, retain valuation;
  digest updates persisted day only after success and uses locker. Test persistence and retry.
- [x] 4. Runtime: .dockerignore, dev_run lifecycle, requirements/installation. Exclude secrets/data from
  image; install missing declared dependencies; prevent duplicate queued demo checks and close container.
- [x] 5. Verify: full pytest, meaningful PostgreSQL/Redis integration if available, SQL checks,
  read-only evaluation against saved data, independent code review, update PROGRESS.md.

## Review focus

Unknown specs must not be treated as known; currency/condition must not be mixed silently;
existing snapshots need segregation when grouping semantics change; restart must retain dedup;
temporary failures must preserve retry opportunities. New SQL must work on real PostgreSQL.

## Execution ledger

Baseline: existing 215 unit tests; integration requires PostgreSQL/Redis.
Decision: retain uncommitted user changes and leave fixes reviewable without automatic commit/push.


### Completion evidence

- Regression tests observed RED before fixes: 7 median regressions; AI/cooldown; free-item retry,
  job persistence, scheduler shutdown; review findings, legacy-state conversion and chart outliers.
- Installed declared missing dependencies in project .venv; pip check clean.
- Final full suite against real PostgreSQL 16 and Redis: **252 passed in 6.70s**, no skipped tests.
- Alembic 0001–0009 on an empty isolated PostgreSQL database: successful.
- All SQL PREPARE checks: 0 failures; SQL semantic checks: all passed.
- Independent review: review_fixes; all four P2 findings fixed with RED→GREEN regression tests.
- Decision: unknown condition/currency/configuration cannot establish a deal; mixed models and
  conditions use separate card groups. Exact discount thresholds are checked before display rounding.
- Decision: corrected snapshots use a v3 namespace including model/condition/currency; legacy
  snapshots stay in the file/database but do not contaminate the new comparison history.
- Decision: temporary /tmp PostgreSQL and Redis used for tests; existing live bot remains in file mode.
- Existing user changes preserved; no automatic commit, merge or push.
