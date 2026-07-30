import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib import error as urlerror
from urllib import parse, request

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters


FALSE_VALUES = {"0", "false", "no", "off"}
TRUE_VALUES = {"1", "true", "yes", "on"}
SUPPORTED_MODES = {"copy", "forward"}
DEFAULT_ALLOWED_UPDATES = ("message", "channel_post")
DEFAULT_WEBHOOK_PATH = "/lamaree-tilda"
DEFAULT_DB_FILE = "/var/lib/lamaree-telegram-router/router.sqlite3"
WEBHOOK_SERVICE_KEYS = {
    "tranid",
    "transaction id",
    "transaction_id",
    "formid",
    "block id",
    "block_id",
    "form name",
    "form_name",
    "formname",
    "tildaspec-formname",
    "tildaspec-formid",
    "tildaspec-pageid",
    "tildaspec-projectid",
    "tildaspec-referer",
    "tildaspec-url",
    "tildaspec-cookie",
    "cookies",
}
WEBHOOK_FIELD_LABELS = {
    "restoraunt": "Ресторан",
    "restaurant": "Ресторан",
    "name": "Имя",
    "name-2": "Имя",
    "company": "Компания",
    "type": "Тип мероприятия",
    "date": "Дата",
    "time": "Время",
    "quantity": "Количество гостей",
    "place": "Место",
    "contact": "Контактное лицо",
    "massage-type": "Способ связи",
    "message-type": "Способ связи",
    "contact-type": "Способ связи",
    "massage-id": "Контакт",
    "message-id": "Контакт",
    "phone": "Телефон",
    "telephone": "Телефон",
    "tel": "Телефон",
    "email": "Email",
    "textarea": "Комментарий",
    "comment": "Комментарий",
    "comments": "Комментарий",
    "message": "Комментарий",
    "adress": "Адрес",
    "address": "Адрес",
    "city": "Город",
    "checkbox": "Согласие на обработку данных",
}
CONTACT_METHOD_LABELS = {
    "phone": "Телефон",
    "telephone": "Телефон",
    "tel": "Телефон",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "email": "Email",
}


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("lamaree-router")


def normalize(text: object) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default

    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False

    raise ValueError(f"{name} должен быть boolean: 1/0, true/false, yes/no, on/off")


def parse_int(name: str, default: Optional[int] = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        if default is None:
            raise ValueError(f"Не указан {name}")
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть целым числом") from exc


def parse_optional_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None

    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть целым числом") from exc


def parse_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default

    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть числом") from exc


def parse_allowed_updates() -> tuple[str, ...]:
    raw = os.getenv("ALLOWED_UPDATES")
    if raw is None or raw.strip() == "":
        return DEFAULT_ALLOWED_UPDATES

    updates = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not updates:
        raise ValueError("ALLOWED_UPDATES не может быть пустым")

    return updates


@dataclass(frozen=True)
class Settings:
    bot_token: str
    source_chat_id: int
    routes_file: str
    mode: str
    unknown_chat_id: Optional[int]
    health_enabled: bool
    health_host: str
    health_port: int
    log_heartbeat_interval: int
    poll_interval: float
    poll_timeout: int
    bootstrap_retries: int
    drop_pending_updates: bool
    ignore_bot_messages: bool
    allowed_updates: tuple[str, ...]
    webhook_enabled: bool
    webhook_host: str
    webhook_port: int
    webhook_path: str
    webhook_max_body_bytes: int
    webhook_send_to_source: bool
    db_file: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("Не указан BOT_TOKEN")

        mode = os.getenv("MODE", "copy").strip().lower()
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"MODE должен быть одним из: {', '.join(sorted(SUPPORTED_MODES))}")

        source_chat_id = parse_int("SOURCE_CHAT_ID")
        health_port = parse_int("PORT") if os.getenv("PORT") else parse_int("HEALTH_PORT", 8080)
        poll_interval = parse_float("POLL_INTERVAL", 0)
        poll_timeout = parse_int("POLL_TIMEOUT", 30)
        heartbeat_interval = parse_int("LOG_HEARTBEAT_INTERVAL", 300)
        webhook_port = parse_int("WEBHOOK_PORT", 8096)
        webhook_path = os.getenv("WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH).strip() or DEFAULT_WEBHOOK_PATH
        webhook_max_body_bytes = parse_int("WEBHOOK_MAX_BODY_BYTES", 256 * 1024)

        if not 1 <= health_port <= 65535:
            raise ValueError("HEALTH_PORT/PORT должен быть в диапазоне 1..65535")
        if not 1 <= webhook_port <= 65535:
            raise ValueError("WEBHOOK_PORT должен быть в диапазоне 1..65535")
        if not webhook_path.startswith("/") or webhook_path == "/":
            raise ValueError("WEBHOOK_PATH должен начинаться с / и не может быть корнем сайта")
        if poll_interval < 0:
            raise ValueError("POLL_INTERVAL не может быть отрицательным")
        if poll_timeout < 1:
            raise ValueError("POLL_TIMEOUT должен быть больше 0")
        if heartbeat_interval < 0:
            raise ValueError("LOG_HEARTBEAT_INTERVAL не может быть отрицательным")
        if webhook_max_body_bytes < 1024:
            raise ValueError("WEBHOOK_MAX_BODY_BYTES должен быть не меньше 1024")

        return cls(
            bot_token=bot_token,
            source_chat_id=source_chat_id,
            routes_file=os.getenv("ROUTES_FILE", "routes.json").strip() or "routes.json",
            mode=mode,
            unknown_chat_id=parse_optional_int("UNKNOWN_CHAT_ID"),
            health_enabled=parse_bool("HEALTH_ENABLED", True),
            health_host=os.getenv("HEALTH_HOST", "0.0.0.0").strip() or "0.0.0.0",
            health_port=health_port,
            log_heartbeat_interval=heartbeat_interval,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            bootstrap_retries=parse_int("BOOTSTRAP_RETRIES", -1),
            drop_pending_updates=parse_bool("DROP_PENDING_UPDATES", False),
            ignore_bot_messages=parse_bool("IGNORE_BOT_MESSAGES", True),
            allowed_updates=parse_allowed_updates(),
            webhook_enabled=parse_bool("WEBHOOK_ENABLED", False),
            webhook_host=os.getenv("WEBHOOK_HOST", "127.0.0.1").strip() or "127.0.0.1",
            webhook_port=webhook_port,
            webhook_path=webhook_path.rstrip("/"),
            webhook_max_body_bytes=webhook_max_body_bytes,
            webhook_send_to_source=parse_bool("WEBHOOK_SEND_TO_SOURCE", True),
            db_file=os.getenv("DB_FILE", DEFAULT_DB_FILE).strip() or DEFAULT_DB_FILE,
        )


@dataclass(frozen=True)
class Rule:
    field: str
    contains: str


@dataclass(frozen=True)
class Route:
    name: str
    target_chat_id: int
    rules: tuple[Rule, ...]
    match: str = "any"

    @property
    def sort_weight(self) -> tuple[int, int, int]:
        return (
            1 if self.match == "all" else 0,
            len(self.rules),
            max((len(rule.contains) for rule in self.rules), default=0),
        )


@dataclass
class RuntimeState:
    started_at: float = field(default_factory=time.time)
    polling_started_at: Optional[float] = None
    last_update_at: Optional[float] = None
    last_route_at: Optional[float] = None
    last_error_at: Optional[float] = None
    last_error: Optional[str] = None
    updates_seen: int = 0
    messages_routed: int = 0
    messages_unknown: int = 0
    messages_ignored: int = 0
    webhooks_seen: int = 0
    webhooks_routed: int = 0
    webhook_duplicates: int = 0
    webhook_bad_requests: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seconds_since(self, timestamp: Optional[float]) -> Optional[int]:
        if timestamp is None:
            return None
        return int(time.time() - timestamp)

    def mark_polling_started(self) -> None:
        with self.lock:
            self.polling_started_at = time.time()

    def mark_update_seen(self) -> None:
        with self.lock:
            self.last_update_at = time.time()
            self.updates_seen += 1

    def mark_routed(self) -> None:
        with self.lock:
            self.last_route_at = time.time()
            self.messages_routed += 1

    def mark_unknown(self) -> None:
        with self.lock:
            self.messages_unknown += 1

    def mark_ignored(self) -> None:
        with self.lock:
            self.messages_ignored += 1

    def mark_webhook_seen(self) -> None:
        with self.lock:
            self.webhooks_seen += 1

    def mark_webhook_routed(self) -> None:
        with self.lock:
            self.last_route_at = time.time()
            self.webhooks_routed += 1
            self.messages_routed += 1

    def mark_webhook_duplicate(self) -> None:
        with self.lock:
            self.webhook_duplicates += 1

    def mark_webhook_bad_request(self) -> None:
        with self.lock:
            self.webhook_bad_requests += 1

    def mark_error(self, error: BaseException | str) -> None:
        with self.lock:
            self.last_error_at = time.time()
            self.last_error = str(error)[:500]

    def snapshot(self, routes_count: int) -> dict:
        with self.lock:
            return {
                "status": "ok",
                "uptime_seconds": int(time.time() - self.started_at),
                "polling_started_seconds_ago": self.seconds_since(self.polling_started_at),
                "routes": routes_count,
                "updates_seen": self.updates_seen,
                "messages_routed": self.messages_routed,
                "messages_unknown": self.messages_unknown,
                "messages_ignored": self.messages_ignored,
                "webhooks_seen": self.webhooks_seen,
                "webhooks_routed": self.webhooks_routed,
                "webhook_duplicates": self.webhook_duplicates,
                "webhook_bad_requests": self.webhook_bad_requests,
                "last_update_seconds_ago": self.seconds_since(self.last_update_at),
                "last_route_seconds_ago": self.seconds_since(self.last_route_at),
                "last_error_seconds_ago": self.seconds_since(self.last_error_at),
                "last_error": self.last_error,
            }


@dataclass(frozen=True)
class RouterContext:
    settings: Settings
    routes: tuple[Route, ...]
    state: RuntimeState
    dedup_store: "DedupStore"


class DedupStore:
    def __init__(self, db_file: str) -> None:
        self.db_file = db_file
        self.lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_file, timeout=30)

    def _init_db(self) -> None:
        directory = os.path.dirname(self.db_file)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    route_name TEXT,
                    target_chat_id INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def reserve(self, transaction_id: str) -> bool:
        now = int(time.time())
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO processed_transactions (
                    transaction_id,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, 'processing', ?, ?)
                """,
                (transaction_id, now, now),
            )
            return cursor.rowcount == 1

    def complete(self, transaction_id: str, route: Optional[Route], status: str = "sent") -> None:
        now = int(time.time())
        route_name = route.name if route else None
        target_chat_id = route.target_chat_id if route else None
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE processed_transactions
                   SET status = ?,
                       route_name = ?,
                       target_chat_id = ?,
                       updated_at = ?
                 WHERE transaction_id = ?
                """,
                (status, route_name, target_chat_id, now, transaction_id),
            )

    def release(self, transaction_id: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM processed_transactions WHERE transaction_id = ? AND status = 'processing'",
                (transaction_id,),
            )


def load_routes(path: str) -> tuple[Route, ...]:
    with open(path, "r", encoding="utf-8") as file:
        raw_routes = json.load(file)

    if not isinstance(raw_routes, list):
        raise ValueError("routes.json должен содержать список маршрутов")

    routes: list[Route] = []
    for index, raw_route in enumerate(raw_routes, start=1):
        if not isinstance(raw_route, dict):
            raise ValueError(f"Маршрут #{index} должен быть объектом")

        name = str(raw_route.get("name", "")).strip()
        if not name:
            raise ValueError(f"У маршрута #{index} нет name")

        try:
            target_chat_id = int(raw_route["target_chat_id"])
        except KeyError as exc:
            raise ValueError(f"У маршрута '{name}' нет target_chat_id") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"target_chat_id маршрута '{name}' должен быть числом") from exc

        raw_rules = raw_route.get("rules", [])
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ValueError(f"У маршрута '{name}' нет правил")

        match = normalize(raw_route.get("match")) or "any"
        if match not in {"all", "any"}:
            raise ValueError(f"match маршрута '{name}' должен быть 'all' или 'any'")

        rules: list[Rule] = []
        for rule_index, raw_rule in enumerate(raw_rules, start=1):
            if not isinstance(raw_rule, dict):
                raise ValueError(f"Правило #{rule_index} маршрута '{name}' должно быть объектом")

            contains = normalize(raw_rule.get("contains"))
            if not contains:
                raise ValueError(f"Правило #{rule_index} маршрута '{name}' пустое")

            rules.append(
                Rule(
                    field=normalize(raw_rule.get("field")) or "full_text",
                    contains=contains,
                )
            )

        routes.append(
            Route(
                name=name,
                target_chat_id=target_chat_id,
                rules=tuple(rules),
                match=match,
            )
        )

    routes.sort(key=lambda route: route.sort_weight, reverse=True)
    return tuple(routes)


def extract_field(text: str, field_name: str) -> str:
    pattern = rf"^\s*{re.escape(field_name)}\s*:\s*(.+?)\s*$"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()


def extract_urls(text: str) -> str:
    return " ".join(re.findall(r"https?://\S+", text, flags=re.IGNORECASE))


def get_route_fields(text: str) -> dict[str, str]:
    return {
        "full_text": text,
        "restoraunt": extract_field(text, "restoraunt"),
        "restaurant": extract_field(text, "restaurant"),
        "form_name": extract_field(text, "Form Name"),
        "url": extract_urls(text),
    }


def find_route(text: str, routes: tuple[Route, ...]) -> Optional[Route]:
    fields = get_route_fields(text)
    normalized_fields = {key: normalize(value) for key, value in fields.items()}

    for route in routes:
        matches: list[bool] = []
        for rule in route.rules:
            haystack = normalized_fields.get(rule.field) or normalized_fields["full_text"]
            matches.append(rule.contains in haystack)

        if route.match == "all" and all(matches):
            return route
        if route.match == "any" and any(matches):
            return route

    return None


def normalize_payload_key(key: str) -> str:
    value = key.strip().lower().replace("_", "-")
    value = re.sub(r"\s+", " ", value)
    return value


def get_first_payload_value(pairs: list[tuple[str, str]], *keys: str) -> str:
    normalized_keys = {normalize_payload_key(key) for key in keys}
    for key, value in pairs:
        if normalize_payload_key(key) in normalized_keys and value.strip():
            return value.strip()
    return ""


def parse_webhook_pairs(body: bytes, content_type: str) -> list[tuple[str, str]]:
    media_type = content_type.split(";", 1)[0].strip().lower()
    text = body.decode("utf-8", errors="replace")

    if media_type in {"", "application/x-www-form-urlencoded", "multipart/form-data"}:
        return [(key, value) for key, value in parse.parse_qsl(text, keep_blank_values=True)]

    if media_type == "application/json":
        data = json.loads(text or "{}")
        if not isinstance(data, dict):
            raise ValueError("JSON webhook body должен быть объектом")
        pairs: list[tuple[str, str]] = []
        for key, value in data.items():
            if isinstance(value, list):
                pairs.extend((str(key), str(item)) for item in value)
            else:
                pairs.append((str(key), "" if value is None else str(value)))
        return pairs

    # Fallback for simple key:value payloads.
    pairs = []
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            pairs.append((key.strip(), value.strip()))
    return pairs


def get_webhook_field_label(key: str) -> str:
    normalized_key = normalize_payload_key(key)
    known_label = WEBHOOK_FIELD_LABELS.get(normalized_key)
    if known_label:
        return known_label

    readable_key = re.sub(r"[_-]+", " ", key).strip()
    return readable_key[:1].upper() + readable_key[1:]


def get_webhook_display_value(key: str, value: str) -> str:
    normalized_key = normalize_payload_key(key)
    stripped_value = value.strip()
    normalized_value = normalize(stripped_value)

    if normalized_key == "checkbox":
        if normalized_value in TRUE_VALUES:
            return "Да"
        if normalized_value in FALSE_VALUES:
            return "Нет"

    if normalized_key in {"massage-type", "message-type", "contact-type"}:
        return CONTACT_METHOD_LABELS.get(normalized_value, stripped_value)

    return stripped_value


def get_webhook_title(form_name: str, source_url: str = "") -> str:
    form_marker = normalize(f"{form_name} {source_url}")
    if "бортов" in form_marker or "onboard-catering" in form_marker:
        return "ЗАЯВКА НА БОРТОВОЕ ПИТАНИЕ"
    if "достав" in form_marker or "/delivery" in form_marker:
        return "ЗАЯВКА НА ДОСТАВКУ"
    if "кейтеринг" in form_marker or "/catering" in form_marker:
        return "ЗАЯВКА НА КЕЙТЕРИНГ"
    if "брон" in form_marker or not form_name:
        return "ЗАЯВКА НА БРОНЬ"

    cleaned_form_name = re.sub(
        r"^\s*заявка\s+на\s+",
        "",
        form_name,
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned_form_name:
        return "ЗАЯВКА"
    return f"ЗАЯВКА: {cleaned_form_name.upper()}"


def payload_to_tilda_message(
    pairs: list[tuple[str, str]],
    referer: str = "",
) -> tuple[str, str, str]:
    transaction_id = get_first_payload_value(pairs, "tranid", "Transaction ID", "transaction_id")
    block_id = get_first_payload_value(pairs, "formid", "Block ID", "block_id", "tildaspec-formid")
    form_name = get_first_payload_value(
        pairs,
        "Form Name",
        "form_name",
        "formname",
        "tildaspec-formname",
    )
    source_url = get_first_payload_value(pairs, "url", "tildaspec-url", "tildaspec-referer") or referer

    display_lines = [get_webhook_title(form_name, source_url), ""]
    routing_lines = ["Request details:"]
    for key, value in pairs:
        stripped_value = value.strip()
        if stripped_value:
            routing_lines.append(f"{key}: {stripped_value}")
        if normalize_payload_key(key) in WEBHOOK_SERVICE_KEYS:
            continue
        if not stripped_value:
            continue
        display_lines.append(
            f"{get_webhook_field_label(key)}: {get_webhook_display_value(key, stripped_value)}"
        )

    service_lines = ["", "Служебная информация:"]
    routing_lines.extend(["", "Additional information:"])
    if form_name:
        service_lines.append(f"Тип заявки: {form_name}")
        routing_lines.append(f"Form Name: {form_name}")
    if transaction_id:
        service_lines.append(f"Номер заявки: {transaction_id}")
        routing_lines.append(f"Transaction ID: {transaction_id}")
    if block_id:
        service_lines.append(f"ID блока: {block_id}")
        routing_lines.append(f"Block ID: {block_id}")
    if source_url:
        service_lines.append(f"Страница: {source_url}")
        routing_lines.append(source_url)

    return "\n".join(display_lines + service_lines), "\n".join(routing_lines), transaction_id


def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines():
        line_length = len(line) + 1
        if current and current_length + line_length > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += line_length

    if current:
        chunks.append("\n".join(current))

    return chunks


def send_telegram_message(bot_token: str, chat_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for chunk in split_telegram_text(text):
        payload = parse.urlencode(
            {
                "chat_id": str(chat_id),
                "text": chunk,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        telegram_request = request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with request.urlopen(telegram_request, timeout=30) as response:
                response_body = response.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram sendMessage failed: HTTP {exc.code} {response_body[:300]}") from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"Telegram sendMessage failed: {exc.reason}") from exc

        data = json.loads(response_body)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {str(data)[:300]}")


def get_router_context(context: ContextTypes.DEFAULT_TYPE) -> RouterContext:
    router_context = context.application.bot_data.get("router_context")
    if not isinstance(router_context, RouterContext):
        raise RuntimeError("Router context не инициализирован")
    return router_context


async def send_unknown(
    router_context: RouterContext,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
) -> None:
    if router_context.settings.unknown_chat_id is None:
        return

    await context.bot.copy_message(
        chat_id=router_context.settings.unknown_chat_id,
        from_chat_id=chat_id,
        message_id=message_id,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    router_context = get_router_context(context)
    router_context.state.mark_update_seen()

    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        router_context.state.mark_ignored()
        return

    if chat.id != router_context.settings.source_chat_id:
        router_context.state.mark_ignored()
        return

    if router_context.settings.ignore_bot_messages and message.from_user and message.from_user.is_bot:
        router_context.state.mark_ignored()
        return

    text = message.text or message.caption or ""
    if not text:
        router_context.state.mark_ignored()
        logger.info("Сообщение без текста/подписи пропущено: chat_id=%s message_id=%s", chat.id, message.message_id)
        return

    route = find_route(text, router_context.routes)
    if route is None:
        router_context.state.mark_unknown()
        logger.warning("Маршрут не найден: chat_id=%s message_id=%s text=%s", chat.id, message.message_id, text[:300])
        await send_unknown(router_context, context, chat.id, message.message_id)
        return

    if router_context.settings.mode == "forward":
        await context.bot.forward_message(
            chat_id=route.target_chat_id,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
    else:
        await context.bot.copy_message(
            chat_id=route.target_chat_id,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )

    router_context.state.mark_routed()
    logger.info("Заявка отправлена в '%s' / chat_id=%s", route.name, route.target_chat_id)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    router_context = context.application.bot_data.get("router_context")
    if isinstance(router_context, RouterContext):
        router_context.state.mark_error(context.error or "unknown error")

    if isinstance(context.error, BaseException):
        logger.error(
            "Ошибка при обработке update=%s",
            update,
            exc_info=(type(context.error), context.error, context.error.__traceback__),
        )
    else:
        logger.error("Ошибка при обработке update=%s: %s", update, context.error)


def make_health_handler(router_context: RouterContext):
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path not in {"/", "/health", "/healthz"}:
                self.send_response(404)
                self.end_headers()
                return

            payload = router_context.state.snapshot(routes_count=len(router_context.routes))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("Health check: " + format, *args)

    return HealthHandler


def start_health_server(router_context: RouterContext) -> None:
    settings = router_context.settings
    if not settings.health_enabled:
        logger.info("Health-check server disabled")
        return

    try:
        server = ThreadingHTTPServer((settings.health_host, settings.health_port), make_health_handler(router_context))
    except OSError:
        logger.exception("Не удалось запустить health-check сервер на %s:%s", settings.health_host, settings.health_port)
        return

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    thread.start()
    logger.info("Health-check server started on %s:%s", settings.health_host, settings.health_port)


def make_webhook_handler(router_context: RouterContext):
    class WebhookHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not self._is_webhook_path():
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            self._send_json(200, {"ok": True, "status": "ready"})

        def do_POST(self) -> None:
            settings = router_context.settings
            state = router_context.state

            if not self._is_webhook_path():
                self._send_json(404, {"ok": False, "error": "not_found"})
                return

            state.mark_webhook_seen()
            transaction_id = ""
            route: Optional[Route] = None
            reserved = False

            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    state.mark_webhook_bad_request()
                    self._send_json(400, {"ok": False, "error": "empty_body"})
                    return
                if length > settings.webhook_max_body_bytes:
                    state.mark_webhook_bad_request()
                    self._send_json(413, {"ok": False, "error": "body_too_large"})
                    return

                body = self.rfile.read(length)
                pairs = parse_webhook_pairs(body, self.headers.get("Content-Type", ""))
                if not pairs:
                    state.mark_webhook_bad_request()
                    self._send_json(400, {"ok": False, "error": "empty_payload"})
                    return

                message_text, routing_text, transaction_id = payload_to_tilda_message(
                    pairs,
                    referer=self.headers.get("Referer", ""),
                )

                if transaction_id:
                    reserved = router_context.dedup_store.reserve(transaction_id)
                    if not reserved:
                        state.mark_webhook_duplicate()
                        self._send_json(200, {"ok": True, "status": "duplicate", "transaction_id": transaction_id})
                        logger.info("Webhook duplicate ignored: transaction_id=%s", transaction_id)
                        return

                route = find_route(routing_text, router_context.routes)
                if route is None:
                    state.mark_unknown()
                    if settings.webhook_send_to_source:
                        self._send_source_message(message_text)
                    if settings.unknown_chat_id is not None:
                        send_telegram_message(settings.bot_token, settings.unknown_chat_id, message_text)
                    if transaction_id:
                        router_context.dedup_store.complete(transaction_id, None, status="no_route")
                    self._send_json(200, {"ok": True, "status": "no_route", "transaction_id": transaction_id})
                    logger.warning("Webhook route not found: transaction_id=%s", transaction_id or "-")
                    return

                send_telegram_message(settings.bot_token, route.target_chat_id, message_text)
                if settings.webhook_send_to_source:
                    try:
                        self._send_source_message(message_text)
                    except Exception as exc:
                        state.mark_error(exc)
                        logger.exception(
                            "Webhook routed, but failed to copy to source chat: transaction_id=%s route=%s",
                            transaction_id or "-",
                            route.name,
                        )

                if transaction_id:
                    router_context.dedup_store.complete(transaction_id, route)

                state.mark_webhook_routed()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "status": "routed",
                        "transaction_id": transaction_id,
                        "route": route.name,
                        "target_chat_id": route.target_chat_id,
                    },
                )
                logger.info(
                    "Webhook routed: transaction_id=%s route=%s target_chat_id=%s",
                    transaction_id or "-",
                    route.name,
                    route.target_chat_id,
                )
            except Exception as exc:
                if reserved and transaction_id:
                    router_context.dedup_store.release(transaction_id)
                state.mark_error(exc)
                logger.exception(
                    "Webhook processing failed: transaction_id=%s route=%s",
                    transaction_id or "-",
                    route.name if route else "-",
                )
                self._send_json(500, {"ok": False, "error": "processing_failed"})

        def _is_webhook_path(self) -> bool:
            path = self.path.split("?", 1)[0].rstrip("/")
            return secrets.compare_digest(path, router_context.settings.webhook_path)

        def _send_source_message(self, message_text: str) -> None:
            send_telegram_message(
                router_context.settings.bot_token,
                router_context.settings.source_chat_id,
                message_text,
            )

        def _send_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("Webhook HTTP: " + format, *args)

    return WebhookHandler


def start_webhook_server(router_context: RouterContext) -> None:
    settings = router_context.settings
    if not settings.webhook_enabled:
        logger.info("Tilda webhook server disabled")
        return

    try:
        server = ThreadingHTTPServer((settings.webhook_host, settings.webhook_port), make_webhook_handler(router_context))
    except OSError:
        logger.exception("Не удалось запустить Tilda webhook на %s:%s", settings.webhook_host, settings.webhook_port)
        return

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="tilda-webhook-server")
    thread.start()
    logger.info("Tilda webhook server started on %s:%s%s", settings.webhook_host, settings.webhook_port, settings.webhook_path)


def start_log_heartbeat(router_context: RouterContext) -> None:
    interval = router_context.settings.log_heartbeat_interval
    if interval <= 0:
        return

    def log_alive() -> None:
        while True:
            time.sleep(interval)
            snapshot = router_context.state.snapshot(routes_count=len(router_context.routes))
            logger.info(
                "Bot heartbeat: uptime=%ss routes=%s updates=%s routed=%s unknown=%s ignored=%s last_update=%s",
                snapshot["uptime_seconds"],
                snapshot["routes"],
                snapshot["updates_seen"],
                snapshot["messages_routed"],
                snapshot["messages_unknown"],
                snapshot["messages_ignored"],
                snapshot["last_update_seconds_ago"],
            )

    thread = threading.Thread(target=log_alive, daemon=True, name="log-heartbeat")
    thread.start()


def build_application(router_context: RouterContext):
    settings = router_context.settings
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(settings.poll_timeout + 10)
        .get_updates_write_timeout(30)
        .get_updates_pool_timeout(30)
        .build()
    )
    app.bot_data["router_context"] = router_context
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_error_handler(handle_error)
    return app


def main() -> None:
    settings = Settings.from_env()
    routes = load_routes(settings.routes_file)
    router_context = RouterContext(
        settings=settings,
        routes=routes,
        state=RuntimeState(),
        dedup_store=DedupStore(settings.db_file),
    )

    start_health_server(router_context)
    start_webhook_server(router_context)
    start_log_heartbeat(router_context)

    app = build_application(router_context)
    logger.info(
        "La Marée router bot started: mode=%s source_chat_id=%s routes=%s allowed_updates=%s",
        settings.mode,
        settings.source_chat_id,
        len(routes),
        ",".join(settings.allowed_updates),
    )
    router_context.state.mark_polling_started()

    try:
        app.run_polling(
            allowed_updates=settings.allowed_updates,
            poll_interval=settings.poll_interval,
            timeout=settings.poll_timeout,
            bootstrap_retries=settings.bootstrap_retries,
            drop_pending_updates=settings.drop_pending_updates,
        )
    except Exception as exc:
        router_context.state.mark_error(exc)
        logger.exception("Критическая ошибка polling. Процесс завершится, systemd перезапустит сервис.")
        raise


if __name__ == "__main__":
    main()
