import os
import json
import logging
import re
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHAT_ID = int(os.getenv("SOURCE_CHAT_ID", "0"))
ROUTES_FILE = os.getenv("ROUTES_FILE", "routes.json")
MODE = os.getenv("MODE", "copy").lower()
UNKNOWN_CHAT_ID = os.getenv("UNKNOWN_CHAT_ID")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)

if not BOT_TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN")

def normalize(text: str) -> str:
    text = str(text or "").lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def load_routes():
    with open(ROUTES_FILE, "r", encoding="utf-8") as f:
        routes = json.load(f)

    prepared = []
    for route in routes:
        prepared_rules = []
        for rule in route.get("rules", []):
            prepared_rules.append({
                "field": normalize(rule.get("field")),
                "contains": normalize(rule.get("contains"))
            })

        prepared.append({
            "name": route["name"],
            "target_chat_id": int(route["target_chat_id"]),
            "rules": prepared_rules
        })

    # Более длинные правила проверяем первыми.
    # Это важно, чтобы "Бортовой Кейтеринг" не попал в обычный "Кейтеринг".
    prepared.sort(
        key=lambda r: max([len(rule["contains"]) for rule in r["rules"]] or [0]),
        reverse=True
    )
    return prepared

ROUTES = load_routes()

def extract_field(text: str, field_name: str) -> str:
    """
    Ищет поле формата:
    field: value

    Поддерживает любые поля из Tilda:
    restoraunt:
    Form Name:
    name_2:
    Type:
    и т.д.
    """
    pattern = rf"^\s*{re.escape(field_name)}\s*:\s*(.+?)\s*$"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()

def extract_urls(text: str) -> str:
    urls = re.findall(r"https?://\S+", text, flags=re.IGNORECASE)
    return " ".join(urls)

def get_route_fields(text: str) -> dict:
    return {
        "full_text": text,
        "restoraunt": extract_field(text, "restoraunt"),
        "restaurant": extract_field(text, "restaurant"),
        "form_name": extract_field(text, "Form Name"),
        "url": extract_urls(text)
    }

def find_route(text: str):
    fields = get_route_fields(text)
    normalized_fields = {key: normalize(value) for key, value in fields.items()}

    for route in ROUTES:
        for rule in route["rules"]:
            field_name = rule["field"]
            needle = rule["contains"]

            haystack = normalized_fields.get(field_name, "")

            # Если вдруг поле не указано или не найдено, можно искать по всему тексту.
            if not haystack:
                haystack = normalized_fields["full_text"]

            if needle and needle in haystack:
                return route

    return None

async def send_unknown(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    if UNKNOWN_CHAT_ID:
        await context.bot.copy_message(
            chat_id=int(UNKNOWN_CHAT_ID),
            from_chat_id=chat_id,
            message_id=message_id
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    if chat.id != SOURCE_CHAT_ID:
        return

    if message.from_user and message.from_user.is_bot:
        return

    text = message.text or message.caption or ""

    if not text:
        logging.info("Сообщение без текста/подписи пропущено")
        return

    route = find_route(text)

    if not route:
        logging.warning("Маршрут не найден. Сообщение: %s", text[:300])
        await send_unknown(context, chat.id, message.message_id)
        return

    if MODE == "forward":
        await context.bot.forward_message(
            chat_id=route["target_chat_id"],
            from_chat_id=chat.id,
            message_id=message.message_id
        )
    else:
        await context.bot.copy_message(
            chat_id=route["target_chat_id"],
            from_chat_id=chat.id,
            message_id=message.message_id
        )

    logging.info("Заявка отправлена в '%s' / chat_id=%s", route["name"], route["target_chat_id"])

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    print("La Marée router bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
