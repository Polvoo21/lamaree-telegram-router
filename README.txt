La Marée Telegram Router Bot

Запуск:
python bot.py

Обязательные переменные окружения:
BOT_TOKEN - токен Telegram-бота
SOURCE_CHAT_ID - chat_id исходного чата, откуда бот принимает заявки

Необязательные переменные:
ROUTES_FILE - путь к routes.json, по умолчанию routes.json
MODE - copy или forward, по умолчанию copy
UNKNOWN_CHAT_ID - chat_id для заявок без найденного маршрута

Защита от "засыпания" и зависаний на хостинге:
HEALTH_ENABLED - 1/0, включает HTTP health-check, по умолчанию 1
PORT или HEALTH_PORT - порт health-check сервера, по умолчанию 8080
LOG_HEARTBEAT_INTERVAL - интервал heartbeat-лога в секундах, по умолчанию 300
BOOTSTRAP_RETRIES - повторы подключения к Telegram при старте, по умолчанию -1
DROP_PENDING_UPDATES - 1/0, сбрасывать накопившиеся Telegram updates при старте, по умолчанию 0
IGNORE_BOT_MESSAGES - 1/0, игнорировать сообщения от других ботов в polling, по умолчанию 1
ALLOWED_UPDATES - типы updates через запятую, по умолчанию message,channel_post
WEBHOOK_ENABLED - 1/0, включает прямой Tilda Webhook, по умолчанию 0
WEBHOOK_HOST - host webhook-сервера, на VPS используется 0.0.0.0
WEBHOOK_PORT - порт webhook-сервера, на VPS используется 8096
WEBHOOK_PATH - секретный путь webhook, который указывается в Tilda
WEBHOOK_SEND_TO_SOURCE - 1/0, дублировать заявку в общий чат, по умолчанию 1
DB_FILE - sqlite-файл для дедупликации Transaction ID

Production:
На VPS бот запускается через systemd-сервис lamaree-telegram-router.
Перезапуск процесса делает systemd: Restart=always, RestartSec=10.
Для Tilda нужно использовать прямой Webhook URL, а не схему "TildaForms bot -> общий Telegram чат -> router bot".
Telegram не отдаёт одному боту сообщения, отправленные другим ботом.

Текущая маршрутизация:
- 5 ресторанных чатов: брони и доставка по выбранному ресторану
- 1 чат кейтеринга: все заявки идут в Смоленку, ресторан в форме не нужен
- 2 чата бортового питания: Петровка и Санкт-Петербург

Health-check:
GET /health

Пример ответа:
{"status":"ok","uptime_seconds":123,"routes":8,"updates_seen":10,"messages_routed":9}
