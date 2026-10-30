# Telegram Market Monitor

Многопользовательский Telegram-бот по подписке: пользователь создаёт поиски (ключевые слова, цена,
город, состояние…), сервис регулярно проверяет источники объявлений и присылает ссылку на каждое новое
подходящее объявление. Интерфейс бота — на польском.

> **Важно: реальные площадки сейчас отключены.** Регламент OLX.pl (п. 2.3) запрещает агрегировать
> данные сервиса, правила Allegro (п. 4.1(e)) запрещают scraping без согласия. Подробно и с источниками —
> [`docs/SOURCES.md`](docs/SOURCES.md). Весь цикл работает и протестирован на **тестовом источнике**:
> объявления помечены `[TEST]`, ссылки ведут на `example.invalid`.

> **Личный режим** (`PERSONAL_MODE=true`): бот только для владельца, без оплаты, читает публичные
> результаты поиска OLX раз в 10+ минут — см. [`docs/PERSONAL_MODE.md`](docs/PERSONAL_MODE.md) (там же риски).
> Проверка доступа к OLX с вашего компьютера: `python3 scripts/olx_probe.py rower`.

## Что умеет первая версия
* Регистрация, бесплатный пробный период (один раз), тарифы Basic / Pro / Premium (лимиты в БД, меняются
  командой администратора).
* Пошаговое создание поиска кнопками (10 шагов): название, источники, обязательные / альтернативные /
  исключающие слова, категория, цена, состояние, город, способ получения; затем частота проверок,
  поведение при отсутствующих данных, допуск опечаток (Pro+).
* Сопоставление без нейросети: нормализация польского текста, учёт окончаний, точные числа, допуск
  опечаток, цена/валюта/категория/город/радиус/доставка, объяснение причин совпадения.
* Планировщик: справедливое распределение между пользователями, Redis-блокировки, экспоненциальная
  задержка при ошибках, пауза источника при 403/429/CAPTCHA, восстановление после перезапуска.
* Уведомления: одно сообщение на объявление даже при нескольких совпавших поисках, кнопки
  «Otwórz ogłoszenie» / «Wstrzymaj» / «Ustawienia», часы тишины, дневной лимит, уведомление о снижении цены (Pro+).
* Оплата Telegram Stars (XTR): pre-checkout, выдача доступа только после `successful_payment`,
  идемпотентность, продление, смена тарифа с пересчётом, возвраты. **По умолчанию выключена.**
* Администрирование по Telegram ID: статистика, источники, тарифы, ошибки платежей, блокировка,
  выдача подписки, возврат, журнал действий.
* Безопасность: секреты только в `.env`, маскирование токенов в логах, проверка прав на сервере,
  ограничение частоты команд, сроки хранения данных, удаление аккаунта, резервные копии.

## Быстрый старт

### Вариант A — демо без Docker (5 минут)
Нужен только Python 3.11+ (на Mac: https://www.python.org/downloads/ или `brew install python@3.12`).
Данные хранятся в памяти и пропадают при перезапуске; работает только тестовый источник; проверки раз в минуту.
```bash
cd telegram-market
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# в .env должны быть BOT_TOKEN и ADMIN_IDS
python -m app.dev_run
```
В Telegram: `/start` → «Nowe wyszukiwanie» → например слово `rower` → сохранить. Сначала придёт сводка
первой проверки, через 1–2 минуты — уведомление о новом тестовом объявлении.

### Вариант B — полноценный запуск (Docker)
Нужен Docker Desktop (Mac/Windows) или Docker Engine (Linux).
```bash
cp .env.example .env    # заполнить BOT_TOKEN, ADMIN_IDS, POSTGRES_PASSWORD, SUPPORT_CONTACT
docker compose up -d --build
docker compose logs -f bot worker
curl http://127.0.0.1:8080/ready
```
Сервисы: `postgres`, `redis`, `migrate` (alembic upgrade head), `bot`, `worker` (ARQ), `api` (health).

### Тесты
```bash
# без зависимостей — только стандартная библиотека Python:
python3 -m unittest discover -s tests/unit -t .
# полный набор в Docker (unit + интеграция с PostgreSQL/Redis + проверка всех SQL-запросов):
docker compose --profile tests run --rm tests
```

## Документация
| Файл | О чём |
|---|---|
| [docs/AI.md](docs/AI.md) | советник AI: оценка объявления, вопросы, сравнение, настройка ключа |
| [docs/WINDOWS.md](docs/WINDOWS.md) | личный режим 24/7 на Windows-ПК, локальный ИИ (Ollama/Qwen), `/pack` — только горячие предложения |
| [docs/PERSONAL_MODE.md](docs/PERSONAL_MODE.md) | личный режим с OLX: как работает, риски, включение |
| [docs/SOURCES.md](docs/SOURCES.md) | правовая проверка OLX / Allegro Lokalnie, что нужно для включения |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | процессы, слои, цикл мониторинга |
| [docs/PAYMENTS.md](docs/PAYMENTS.md) | Telegram Stars, режимы, возвраты, почему криптооплата заблокирована |
| [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md) | настройка бота, нужные ключи и разрешения |
| [docs/DEPLOY.md](docs/DEPLOY.md) | развёртывание на сервере, обновление, масштабирование |
| [docs/BACKUP.md](docs/BACKUP.md) | резервное копирование и восстановление |
| [docs/SECURITY.md](docs/SECURITY.md) | секреты и их ротация, права, персональные данные |
| [PLAN.md](PLAN.md), [PROGRESS.md](PROGRESS.md) | план работ и текущее состояние |

## Команды бота
Пользователь: `/start`, `/newsearch`, `/searches`, `/status`, `/plan`, `/pause`, `/resume`, `/delete`,
`/settings`, `/help`, `/terms`, `/privacy`, `/support`, `/paysupport`, `/cancel`.

Администратор (`ADMIN_IDS`): `/admin`, `/admin_sources`, `/source_off <код> [причина]`, `/source_on <код>`,
`/plans_admin`, `/plan_set <тариф> <поле> <значение>`, `/payerrors`, `/suspend <tg_id> [причина]`,
`/unsuspend <tg_id>`, `/grant <tg_id> <тариф> <дни>`, `/refund <charge_id> [причина]`, `/audit`.

## Настройки
Все параметры — переменные окружения, см. [`.env.example`](.env.example). Главные: `BOT_TOKEN`,
`ADMIN_IDS`, `PAYMENTS_MODE` (`disabled` / `test` / `live` + `PAYMENTS_LIVE_CONFIRMED=yes`),
`ENABLED_SOURCES`, `DATABASE_URL`, `REDIS_URL`.
