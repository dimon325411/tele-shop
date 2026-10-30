# Инструкции для будущих сессий

Проект: коммерческий Telegram-бот мониторинга объявлений (Allegro Lokalnie, OLX.pl) по подписке.
Полное ТЗ — в инструкциях проекта claude.ai «telegram-market-bot».

## Перед началом работы
1. Прочитать `PROGRESS.md`, `PLAN.md`, этот файл, `git log --oneline | head`.
2. Прогнать тесты: `python3 -m unittest discover -s tests/unit -t .` (только stdlib + pydantic).
3. Если есть PostgreSQL и `psql`: `python3 scripts/check_sql.py "<dsn>"` и
   `python3 scripts/check_sql_semantics.py "<dsn без dbname>"`.
4. Полный набор: `docker compose --profile tests run --rm tests`.

## Правила
* Реальные площадки НЕ включать, пока нет подтверждённого легального доступа (`docs/SOURCES.md`).
  Никакого обхода CAPTCHA, авторизации, антибот-защиты, лимитов. Mock-данные всегда помечены
  `is_test`, `[TEST]`, домен `example.invalid`.
* Платежи: только Telegram Stars (`XTR`). `PAYMENTS_MODE=live` не включать без явного разрешения владельца.
  Криптооплата заблокирована правилами Telegram для цифровых товаров.
* Все тексты интерфейса — в `app/i18n/pl.py` (польский). Новые ключи добавлять туда; тест
  `test_all_used_i18n_keys_exist` ловит отсутствующие.
* Проверки прав — только в сервисах (`app/services`), не в обработчиках.
* Логика без aiogram: `app/wizard.py` (диалог), `app/controller.py` (команды), `app/presentation.py`
  (тексты). Обработчики `app/bot/handlers.py` должны оставаться тонкими.
* SQL — только в `app/storage/sql_queries.py` (проверяется скриптами). Любой новый метод хранилища
  реализовать и в `memory.py`, и в `sql.py`, с теми же ограничениями уникальности.
* Время — UTC (`timestamptz`), локальное время только для отображения/часов тишины.
* Не удалять тесты ради зелёного прогона. Не делать `git push` без разрешения.
* Коммиты — небольшими завершёнными этапами; обновлять `PROGRESS.md` в конце сессии.

## Окружение прошлых сессий
Облачная среда без доступа к PyPI: aiogram/SQLAlchemy/ARQ/FastAPI/redis-py запустить не удалось
(запуск кода, скачанного с GitHub в обход PyPI, был запрещён проверкой безопасности). Поэтому
ядро сделано тестируемым на stdlib, а SQL проверяется через `psql` на реальном PostgreSQL.
