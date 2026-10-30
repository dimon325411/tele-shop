# Развёртывание на сервере

Требования: Linux-сервер (1 vCPU, 1–2 ГБ RAM достаточно для старта), Docker Engine + Docker Compose v2.

```bash
# 1. код
git clone <ваш-репозиторий> /opt/telegram-market && cd /opt/telegram-market
# 2. настройки
cp .env.example .env && nano .env      # BOT_TOKEN, ADMIN_IDS, POSTGRES_PASSWORD (сложный!), SUPPORT_CONTACT
# 3. запуск (миграции применяются сервисом migrate автоматически)
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8080/ready
# 4. логи
docker compose logs -f bot worker
```

Обновление:
```bash
./scripts/backup.sh
git pull
docker compose up -d --build     # migrate снова выполнит alembic upgrade head
```

Резервные копии — см. `docs/BACKUP.md`. Добавьте ежедневный cron для `scripts/backup.sh` и копируйте
файлы из `backups/` за пределы сервера.

Масштабирование: можно запустить несколько воркеров (`docker compose up -d --scale worker=2`):
проверка одного поиска защищена Redis-блокировкой, задачи имеют детерминированный ID, очередь
уведомлений разбирается через `FOR UPDATE SKIP LOCKED`, дубликаты исключены уникальными индексами.
Бот запускайте в **одном** экземпляре (long polling).

Мониторинг: `GET /health` (жив ли процесс), `GET /ready` (БД и Redis), `/admin` и `/admin_sources` в боте.
