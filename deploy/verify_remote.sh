#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/lamaree-telegram-router
ENV_FILE=/etc/lamaree-telegram-router/lamaree.env
SERVICE=lamaree-telegram-router

cd "$APP_DIR"

runuser -u lamaree-router -- env \
  PYTHONDONTWRITEBYTECODE=1 \
  BOT_TOKEN=123456:TEST_TOKEN \
  SOURCE_CHAT_ID=-1003970343773 \
  ROUTES_FILE=routes.json \
  HEALTH_ENABLED=0 \
  WEBHOOK_ENABLED=0 \
  LOG_HEARTBEAT_INTERVAL=0 \
  DB_FILE=/tmp/lamaree-router-verify.sqlite3 \
  .venv/bin/python - <<'PY'
import bot

settings = bot.Settings.from_env()
routes = bot.load_routes(settings.routes_file)
router_context = bot.RouterContext(
    settings=settings,
    routes=routes,
    state=bot.RuntimeState(),
    dedup_store=bot.DedupStore(settings.db_file),
)
bot.build_application(router_context)
print(f"routes={len(routes)}")
print("app_build=ok")
PY

rm -f /tmp/lamaree-router-verify.sqlite3

for family in 4 6; do
  result="$(curl "-$family" -sS -m 10 -o /dev/null -w "%{http_code} %{remote_ip} %{time_total}" https://api.telegram.org || true)"
  echo "telegram_ipv${family}=${result}"
done

echo "service_enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
echo "service_active=$(systemctl is-active "$SERVICE" 2>/dev/null || true)"

if grep -q 'PASTE_TELEGRAM_BOT_TOKEN_HERE' "$ENV_FILE"; then
  echo "token=missing"
else
  echo "token=present"
fi
