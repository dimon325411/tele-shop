# Резервное копирование и восстановление

## Копия
```bash
./scripts/backup.sh            # -> backups/market-YYYYmmdd-HHMMSS.sql.gz, хранит 14 последних
KEEP=30 ./scripts/backup.sh    # хранить 30
```
Ежедневно через cron (на сервере):
```
17 3 * * * cd /opt/telegram-market && ./scripts/backup.sh >> backups/backup.log 2>&1
```
Копируйте `backups/` в другое место (другой сервер, облачное хранилище) — копия на том же диске
не защищает от потери сервера. Redis хранит только очередь и блокировки, его копировать не нужно:
всё состояние (расписание проверок, очередь уведомлений) лежит в PostgreSQL.

## Восстановление
```bash
./scripts/restore.sh backups/market-20261024-031700.sql.gz
```
Скрипт спросит подтверждение, остановит `bot`, `worker`, `api`, восстановит базу и запустит их снова.

## Проверка копий
Раз в месяц восстанавливайте последнюю копию в тестовую базу:
```bash
docker compose exec postgres createdb -U market restore_check
gunzip -c backups/<file>.sql.gz | docker compose exec -T postgres psql -U market -d restore_check -q
docker compose exec postgres psql -U market -d restore_check -c "select count(*) from users"
docker compose exec postgres dropdb -U market restore_check
```
