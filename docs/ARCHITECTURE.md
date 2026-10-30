# Архитектура

Модульный монолит, три процесса из одного образа + PostgreSQL + Redis:

```
            Telegram
               │ long polling
         ┌─────▼─────┐        ┌──────────────┐
         │    bot    │        │    worker    │  ARQ: schedule_tick → check_search
         │ (aiogram) │        │ (ARQ cron)   │       deliver_notifications, expire, retention
         └─────┬─────┘        └──────┬───────┘
               │   services (app/services)   │
               └──────────┬───────────────────┘
          ┌───────────────┼───────────────┐
     PostgreSQL         Redis           sources (app/sources)
   (всё состояние)  (очередь, блокировки,   mock ✓ | olx ✗ | allegro_lokalnie ✗
                     FSM диалогов)
         api (FastAPI): /health, /ready, /webhooks/crypto (выключен)
```

## Слои
| Каталог | Что внутри | Зависимости |
|---|---|---|
| `app/domain` | модели, сопоставление, тарифы, валидация, платежи, время | только stdlib + pydantic |
| `app/services` | бизнес-логика: поиски, мониторинг, уведомления, подписки, платежи, админ | domain + интерфейс хранилища |
| `app/storage` | `repo.py` (интерфейс), `memory.py` (тесты), `sql.py` + `sql_queries.py` (PostgreSQL) | SQLAlchemy для sql.py |
| `app/sources` | адаптеры площадок с единым интерфейсом, вежливый HTTP-клиент | — |
| `app/wizard.py`, `app/controller.py`, `app/presentation.py` | логика диалогов и тексты ответов бота без aiogram | services |
| `app/bot` | aiogram: обработчики, middleware, клавиатуры, отправка уведомлений | aiogram |
| `app/worker` | `jobs.py` (тела задач), `main.py` (ARQ) | arq |
| `app/i18n` | все тексты интерфейса (польский), `t(key)` | — |

## Цикл мониторинга
1. `schedule_tick` (каждые 30 с): выбирает поиски с `next_check_at <= now` только у пользователей с
   действующей подпиской, не больше `MAX_CHECKS_PER_USER_PER_TICK` на пользователя, резервирует
   следующий слот (`next_check_at = now + interval`) и ставит задачу `check_search` с ID
   `check:<search>:<slot>` (дубли невозможны).
2. `check_search`: Redis-блокировка на поиск → проверка доступа и лимитов тарифа → для каждого
   источника: состояние источника (выключен/в паузе после блокировки) → `fetch` → upsert объявлений
   (`UNIQUE(source, external_id)`) → сопоставление → `match_results` (`UNIQUE(search, listing)`) →
   постановка уведомления (`UNIQUE(user, dedup_key)`, поэтому одно объявление приходит пользователю
   один раз, даже если совпало с несколькими поисками).
3. Первая проверка поиска — «базовая»: существующие объявления запоминаются без рассылки
   (`NOTIFY_ON_FIRST_RUN=false`), пользователь получает сводку.
4. Ошибки источника: временные → экспоненциальная задержка через `next_check_at`; 403/429/CAPTCHA →
   источник в паузе (`blocked_until`), без попыток обхода.
5. `deliver_notifications` (каждые 10 с): атомарный захват (`pending → sending`, `FOR UPDATE SKIP LOCKED`),
   часы тишины (перенос), дневной лимит тарифа, повторы с задержкой, обработка блокировки бота
   пользователем; «зависшие» после падения воркера записи захватываются снова через 5 минут.

Всё состояние — в PostgreSQL, поэтому перезапуск любого процесса ничего не теряет.
