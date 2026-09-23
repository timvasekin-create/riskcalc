# -*- coding: utf-8 -*-
"""
Telegram-бот RustDeck (@RustDeckcryptobot)

Работает ВНУТРИ FastAPI-процесса фоновым потоком (long polling) —
на бесплатном плане Render отдельный воркер не нужен.
Токен берётся из переменной окружения BOT_TOKEN — НЕ коммитим его в git!

Команды:
  /start [код]  — приветствие + привязка по коду с сайта
  /link <код>   — привязка Telegram к сайту (пробный период 7 дней)
  /prices       — живые цены топ-5 монет
  /status       — статус подписки
  /help         — список команд

База: SQLite (stdlib). ВАЖНО: диск на бесплатном Render эфемерный —
при деплое база сбрасывается. Позже перейдём на постоянное хранилище.
"""
import os
import re
import json
import time
import sqlite3
import secrets
import threading
import urllib.request

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rustdeck.db")

TRIAL_DAYS = 7          # пробный период при привязке
CODE_TTL = 15 * 60      # код привязки живёт 15 минут

_db_lock = threading.Lock()
_bot_username_cache = {"name": None, "ts": 0.0}


# ============================================================
# База данных
# ============================================================
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock:
        conn = _db()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS links (
            code       TEXT PRIMARY KEY,
            status     TEXT NOT NULL DEFAULT 'pending',  -- pending | linked
            created_at REAL NOT NULL,
            chat_id    INTEGER
        );
        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id    INTEGER PRIMARY KEY,
            username   TEXT,
            tier       TEXT NOT NULL DEFAULT 'trial',    -- trial | free | pro
            expires_at REAL,
            linked_at  REAL
        );
        """)
        conn.commit()
        conn.close()


# ============================================================
# Telegram API (чистый urllib, без зависимостей)
# ============================================================
def _tg(method, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(chat_id, text, reply_markup=None):
    """Отправка сообщения (не бросает исключений — бот не должен ронять сайт)."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        _tg("sendMessage", payload)
    except Exception:
        pass


def get_bot_username():
    """Юзернейм бота (кэш на 10 минут) — нужен сайту для deep-link."""
    now = time.time()
    if _bot_username_cache["name"] and now - _bot_username_cache["ts"] < 600:
        return _bot_username_cache["name"]
    try:
        me = _tg("getMe")
        name = me["result"]["username"]
        _bot_username_cache["name"] = name
        _bot_username_cache["ts"] = now
        return name
    except Exception:
        return _bot_username_cache["name"]  # возможно None


# ============================================================
# Привязка сайта ↔ Telegram (используется main.py)
# ============================================================
def create_link_code():
    """Сайт генерирует 6-значный код, юзер отправляет его боту."""
    conn = _db()
    code = None
    for _ in range(20):
        candidate = f"{secrets.randbelow(1000000):06d}"
        if not conn.execute("SELECT 1 FROM links WHERE code=?", (candidate,)).fetchone():
            code = candidate
            break
    if code is None:
        conn.close()
        return None
    conn.execute(
        "INSERT INTO links (code, status, created_at) VALUES (?, 'pending', ?)",
        (code, time.time()),
    )
    conn.commit()
    conn.close()
    return code


def link_code_status(code):
    """Сайт опрашивает: привязался ли юзер по этому коду."""
    if not re.fullmatch(r"\d{6}", code or ""):
        return {"status": "not_found"}
    conn = _db()
    row = conn.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone()
    if not row:
        conn.close()
        return {"status": "not_found"}
    if row["status"] == "linked":
        sub = conn.execute(
            "SELECT * FROM subscribers WHERE chat_id=?", (row["chat_id"],)
        ).fetchone()
        conn.close()
        return {
            "status": "linked",
            "tier": sub["tier"] if sub else "trial",
            "expires_at": sub["expires_at"] if sub else None,
        }
    if time.time() - row["created_at"] > CODE_TTL:
        conn.close()
        return {"status": "expired"}
    conn.close()
    return {"status": "pending"}


# ============================================================
# Команды бота
# ============================================================
def _try_link(chat_id, username, code):
    """Юзер отправил код боту — связываем аккаунты."""
    conn = _db()
    row = conn.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone()
    if not row:
        conn.close()
        send_message(chat_id, "❌ Код не найден. Сгенерируй новый на сайте.")
        return
    if row["status"] == "linked":
        conn.close()
        send_message(chat_id, "ℹ️ Этот код уже использован. Сгенерируй новый на сайте.")
        return
    if time.time() - row["created_at"] > CODE_TTL:
        conn.close()
        send_message(chat_id, "⌛ Код истёк (живёт 15 минут). Сгенерируй новый на сайте.")
        return

    expires = time.time() + TRIAL_DAYS * 86400
    conn.execute(
        "UPDATE links SET status='linked', chat_id=? WHERE code=?", (chat_id, code)
    )
    conn.execute(
        """INSERT INTO subscribers (chat_id, username, tier, expires_at, linked_at)
           VALUES (?, ?, 'trial', ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username""",
        (chat_id, username, expires, time.time()),
    )
    conn.commit()
    conn.close()
    until = time.strftime("%d.%m.%Y", time.localtime(expires))
    send_message(
        chat_id,
        f"✅ *Telegram привязан к RustDeck!*\n\n"
        f"🎁 Пробный период: *{TRIAL_DAYS} дней* (до {until})\n\n"
        f"Скоро здесь появятся уведомления о сделках, SL/TP и дневные сводки.\n"
        f"Команды: /status — подписка, /prices — живые цены, /help — все команды.",
    )


def _cmd_prices(chat_id):
    """Живые цены топ-5 монет (CoinGecko — один запрос, есть и HYPE)."""
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,solana,hyperliquid,binancecoin"
            "&vs_currencies=usd&include_24hr_change=true",
            headers={"User-Agent": "rustdeck-bot /1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        send_message(chat_id, "⚠️ Не удалось получить цены. Попробуй позже.")
        return

    coins = [
        ("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL"),
        ("hyperliquid", "HYPE"), ("binancecoin", "BNB"),
    ]
    lines = ["⚡ *RustDeck — Live Prices*\n"]
    for cg_id, sym in coins:
        d = data.get(cg_id)
        if not d or not d.get("usd"):
            continue
        price = d["usd"]
        change = d.get("usd_24h_change")
        if change is None:
            ch = ""
        else:
            arrow = "🟢 +" if change >= 0 else "🔴 "
            ch = f"  {arrow}{change:.2f}%"
        if price >= 100:
            ps = f"${price:,.2f}"
        elif price >= 1:
            ps = f"${price:.3f}"
        else:
            ps = f"${price:.4f}"
        lines.append(f"*{sym}* {ps}{ch}")
    lines.append("\ncalc.rustdeck.app — рассчитай сделку 🎯")
    send_message(chat_id, "\n".join(lines))


def _cmd_status(chat_id):
    conn = _db()
    sub = conn.execute(
        "SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    conn.close()
    if not sub:
        send_message(
            chat_id,
            "📭 Telegram пока не привязан к сайту.\n"
            "Открой calc.rustdeck.app → «Connect Telegram» в сайдбаре.",
        )
        return
    left_days = (sub["expires_at"] - time.time()) / 86400 if sub["expires_at"] else 0
    if left_days > 0:
        send_message(
            chat_id,
            f"💎 Подписка: *{sub['tier']}*\n"
            f"Осталось: *{max(0, int(left_days)) + 1} дн.*\n\n"
            f"Больше функций: calc.rustdeck.app",
        )
    else:
        send_message(
            chat_id,
            "⌛ Пробный период закончился.\n"
            "Оплата скоро появится — пока всё работает бесплатно 🎁",
        )


def handle_update(update):
    msg = update.get("message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    username = (msg.get("from") or {}).get("username")

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if re.fullmatch(r"/start \d{6}", text) or re.fullmatch(r"\d{6}", payload):
            _try_link(chat_id, username, payload)
        else:
            send_message(
                chat_id,
                "⚡ *RustDeck* — trading tools для крипто-трейдеров\n\n"
                "Калькулятор риска: calc.rustdeck.app\n\n"
                "Чтобы привязать Telegram: открой сайт → «Connect Telegram» → "
                "отправь мне код.\nИли жми кнопку ниже 👇",
                reply_markup={
                    "inline_keyboard": [[
                        {"text": "🧮 Открыть калькулятор", "url": "https://calc.rustdeck.app"}
                    ]]
                },
            )
    elif text.startswith("/link"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        if re.fullmatch(r"\d{6}", code):
            _try_link(chat_id, username, code)
        else:
            send_message(
                chat_id,
                "Формат: `/link 123456`\n"
                "Код сгенерируй на сайте: calc.rustdeck.app → «Connect Telegram».",
            )
    elif text == "/prices":
        _cmd_prices(chat_id)
    elif text == "/status":
        _cmd_status(chat_id)
    elif text == "/help":
        send_message(
            chat_id,
            "*RustDeck — команды:*\n"
            "/prices — живые цены топ-5 монет\n"
            "/status — статус подписки\n"
            "/link <код> — привязать Telegram\n"
            "/start — начать заново\n\n"
            "🧮 Калькулятор: calc.rustdeck.app",
        )
    else:
        send_message(chat_id, "Не понял 🤔 Список команд: /help")


# ============================================================
# Фоновый поток long polling
# ============================================================
def _poll_loop():
    offset = 0
    while True:
        try:
            res = _tg("getUpdates", {"offset": offset, "timeout": 25}, timeout=35)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    handle_update(upd)
                except Exception:
                    pass
        except Exception:
            time.sleep(3)


def start_bot_thread():
    """Запускается из main.py при старте сервера. False — если нет токена."""
    if not BOT_TOKEN:
        return False
    init_db()
    threading.Thread(target=_poll_loop, daemon=True, name="rustdeck-tg-bot").start()
    return True

