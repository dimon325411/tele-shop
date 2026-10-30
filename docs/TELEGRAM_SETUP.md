# Настройка Telegram-бота

1. @BotFather → `/newbot` → имя и username → получить токен → `BOT_TOKEN` в `.env`.
2. Свой числовой ID — у @userinfobot → `ADMIN_IDS` в `.env` (несколько через запятую).
3. (Необязательно) в @BotFather: `/setdescription`, `/setabouttext`, `/setuserpic`.
   Список команд бот выставляет сам при запуске (`set_my_commands`).
4. Платежи Stars для цифровых товаров не требуют платёжного провайдера в @BotFather: `provider_token`
   пустой. Для тестов нужен бот в тестовой среде Telegram (`docs/PAYMENTS.md`).
5. `SUPPORT_CONTACT` — username или ссылка, куда писать в поддержку (показывается в `/support`, `/paysupport`).

Обязательные для платного бота команды есть: `/terms` (условия, принимаются кнопкой до покупки),
`/paysupport` (вопросы по платежам), `/support`, `/privacy`.

## Внешние ключи и разрешения

| Что | Нужно ли сейчас | Где взять |
|---|---|---|
| Токен Telegram-бота | да | @BotFather |
| Тестовый бот для Stars | для проверки оплаты | тестовая среда Telegram |
| Доступ к OLX.pl | для реального мониторинга | письменное согласие / партнёрство OLX (docs/SOURCES.md) |
| Доступ к Allegro Lokalnie | для реального мониторинга | согласие Allegro / официальный API (docs/SOURCES.md) |
| Криптопровайдер | нет (заблокировано правилами Telegram) | — |
