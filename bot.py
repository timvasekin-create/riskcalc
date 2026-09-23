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
        # Миграция: колонка отслеживаемого кошелька
        cols = [r[1] for r in conn.execute("PRAGMA table_info(subscribers)").fetchall()]
        if "watched_wallet" not in cols:
            conn.execute("ALTER TABLE subscribers ADD COLUMN watched_wallet TEXT")
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
# Watch engine: слежение за кошельками 24/7 (для /watch)
# ============================================================
def _hl_post(payload, timeout=10):
    req = urllib.request.Request(
        "https://api-ui.hyperliquid.xyz/info",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wallet_snapshot(addr):
    """Снимок состояния кошелька для сравнения: позиции + ключи филлов."""
    state = _hl_post({"type": "clearinghouseState", "user": addr})
    fills = _hl_post({"type": "userFills", "user": addr})
    positions = {}
    for ap in state.get("assetPositions") or []:
        p = ap.get("position") or {}
        szi = float(p.get("szi") or 0)
        if szi == 0:
            continue
        positions[p.get("coin")] = {
            "side": "Long" if szi > 0 else "Short",
            "size_usd": abs(float(p.get("positionValue") or 0)),
            "entry": float(p.get("entryPx") or 0),
        }
    fill_keys = set()
    for f in (fills or [])[:20]:
        fill_keys.add(
            f"{f.get('time')}|{f.get('coin')}|{f.get('side')}|{f.get('px')}|{f.get('sz')}"
        )
    return {"positions": positions, "fill_keys": fill_keys}


def _watch_loop():
    """Фоновый цикл: раз в 60с опрашивает отслеживаемые кошельки подписчиков."""
    cache = {}  # addr -> snapshot
    while True:
        try:
            conn = _db()
            rows = conn.execute(
                "SELECT chat_id, watched_wallet FROM subscribers WHERE watched_wallet IS NOT NULL"
            ).fetchall()
            conn.close()
            for row in rows:
                addr = (row["watched_wallet"] or "").strip()
                if not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
                    continue
                try:
                    snap = _wallet_snapshot(addr)
                except Exception:
                    continue
                prev = cache.get(addr)
                cache[addr] = snap
                if prev is None:
                    continue  # первый опрос — фиксируем базу

                events = []
                try:
                    fills = _hl_post({"type": "userFills", "user": addr})
                except Exception:
                    fills = []
                for f in sorted(fills or [], key=lambda x: x.get("time") or 0, reverse=True)[:20]:
                    key = f"{f.get('time')}|{f.get('coin')}|{f.get('side')}|{f.get('px')}|{f.get('sz')}"
                    if key in prev["fill_keys"]:
                        continue
                    try:
                        pnl = float(f.get("closedPnl") or 0)
                        px = float(f.get("px") or 0)
                        sz = float(f.get("sz") or 0)
                    except (TypeError, ValueError):
                        continue
                    dir_s = f.get("dir") or f.get("side") or ""
                    if "iquidat" in dir_s:
                        events.append(f"💥 *LIQUIDATION:* {f.get('coin')} {dir_s} @ ${px:,.4g} · PnL ${pnl:+,.2f}")
                    elif pnl != 0:
                        emoji = "🟢" if pnl > 0 else "🔴"
                        events.append(f"{emoji} *Position closed:* {f.get('coin')} {dir_s} @ ${px:,.4g} · PnL ${pnl:+,.2f}")
                    elif "open" in dir_s.lower():
                        events.append(f"🟢 *Position opened:* {f.get('coin')} {dir_s} @ ${px:,.4g} · size {sz:g}")
                    else:
                        events.append(f"🔔 *Fill:* {f.get('coin')} {dir_s} @ ${px:,.4g} · size {sz:g}")

                prev_pos, now_pos = prev["positions"], snap["positions"]
                for c in now_pos:
                    if c not in prev_pos and not any("opened" in e for e in events):
                        p = now_pos[c]
                        events.append(f"🟢 *Position opened:* {c} {p['side']} · ${p['size_usd']:,.0f} @ ${p['entry']:,.4g}")
                for c in prev_pos:
                    if c not in now_pos and not any("closed" in e or "LIQUIDATION" in e for e in events):
                        events.append(f"🔒 *Position closed:* {c}")

                for ev in events:
                    send_message(row["chat_id"], ev)
        except Exception:
            pass
        time.sleep(60)


# ============================================================
# Команды бота
# ============================================================
def _try_link(chat_id, username, code):
    """Юзер отправил код боту — связываем аккаунты."""
    conn = _db()
    row = conn.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone()
    if not row:
        conn.close()
        send_message(chat_id, "❌ Code not found. Generate a new one on the website.")
        return
    if row["status"] == "linked":
        conn.close()
        send_message(chat_id, "ℹ️ This code was already used. Generate a new one on the website.")
        return
    if time.time() - row["created_at"] > CODE_TTL:
        conn.close()
        send_message(chat_id, "⌛ Code expired (codes live for 15 minutes). Generate a new one on the website.")
        return

    expires = time.time() + TRIAL_DAYS * 86400
    conn.execute(
        "UPDATE links SET status='linked', chat_id=? WHERE code=?", (chat_id, code)
    )
    # ОДИН триал на аккаунт: повторная привязка НЕ продлевает подписку.
    existing = conn.execute(
        "SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO subscribers (chat_id, username, tier, expires_at, linked_at)
               VALUES (?, ?, 'trial', ?, ?)""",
            (chat_id, username, expires, time.time()),
        )
        until = time.strftime("%b %d, %Y", time.gmtime(expires))
        message = (
            f"✅ *Telegram linked to RustDeck!*\n\n"
            f"🎁 Free trial: *{TRIAL_DAYS} days* (until {until})\n\n"
            f"👀 Watch any wallet 24/7: send /watch 0x…\n"
            f"(paste the FULL Hyperliquid address — works with any wallet)\n\n"
            f"Commands: /prices — live prices, /status — subscription, /help — all."
        )
    else:
        left_days = (existing["expires_at"] - time.time()) / 86400 if existing["expires_at"] else 0
        if left_days > 0:
            until = time.strftime("%b %d, %Y", time.gmtime(existing["expires_at"]))
            message = (
                f"ℹ️ This Telegram account is already linked.\n\n"
                f"Your plan: *{existing['tier']}* (until {until}).\n"
                f"Linking again does *not* extend the free trial."
            )
        else:
            message = (
                f"ℹ️ This Telegram account is already linked.\n\n"
                f"Your free trial has already been used and has ended.\n"
                f"Paid plans are coming soon — for now everything stays free 🎁"
            )
    conn.commit()
    conn.close()
    send_message(chat_id, message)


def _get_json(url, timeout=8):
    """Простой GET JSON (для внешних API)."""
    req = urllib.request.Request(url, headers={"User-Agent": "rustdeck-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cmd_prices(chat_id):
    """Live top-5 coin prices: Binance (BTC/ETH/SOL/BNB) + Hyperliquid (HYPE).
    CoinGecko is NOT used here: it often blocks datacenter IPs (Render)."""
    prices = {}
    try:
        data = _get_json(
            "https://api.binance.com/api/v3/ticker/24hr"
            "?symbols=%5B%22BTCUSDT%22,%22ETHUSDT%22,%22SOLUSDT%22,%22BNBUSDT%22%5D",
            timeout=8,
        )
        for t in data:
            prices[t["symbol"].replace("USDT", "")] = {
                "price": float(t["lastPrice"]),
                "change": float(t["priceChangePercent"]),
            }
    except Exception:
        pass

    mid = None
    try:
        req = urllib.request.Request(
            "https://api-ui.hyperliquid.xyz/info",
            data=json.dumps({"type": "allMids"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            mids = json.loads(resp.read().decode("utf-8"))
        mid = float(mids.get("@107") or 0)
    except Exception:
        mid = None

    if mid and mid > 0:
        try:
            # Точное 24ч изменение HYPE: берём часовую свечу 24 часа назад
            # (дневная свеча даёт открытие СЕГОДНЯшнего дня — процент врёт)
            req = urllib.request.Request(
                "https://api-ui.hyperliquid.xyz/info",
                data=json.dumps({
                    "type": "candleSnapshot",
                    "req": {
                        "coin": "@107",
                        "interval": "1h",
                        "startTime": int((time.time() - 25 * 3600) * 1000),
                        "endTime": int(time.time() * 1000),
                    },
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                candles = json.loads(resp.read().decode("utf-8"))
            open_24h = float(candles[0]["o"]) if isinstance(candles, list) and candles else 0
            change = (mid - open_24h) / open_24h * 100 if open_24h > 0 else None
            prices["HYPE"] = {"price": mid, "change": change}
        except Exception:
            prices["HYPE"] = {"price": mid, "change": None}

    if not prices:
        send_message(chat_id, "⚠️ Price feed is temporarily unavailable. Try again in a minute.")
        return

    order = ["BTC", "ETH", "SOL", "HYPE", "BNB"]
    lines = ["⚡ *RustDeck — Live Prices*\n"]
    for sym in order:
        p = prices.get(sym)
        if not p:
            continue
        price = p["price"]
        if price >= 100:
            ps = f"${price:,.2f}"
        elif price >= 1:
            ps = f"${price:.3f}"
        else:
            ps = f"${price:.4f}"
        change = p["change"]
        if change is None:
            ch = ""
        else:
            arrow = "🟢 +" if change >= 0 else "🔴 "
            ch = f"  {arrow}{change:.2f}%"
        lines.append(f"*{sym}* {ps}{ch}")
    lines.append("\nrustdeck.app — wallet stats & market tools 🎯")
    send_message(chat_id, "\n".join(lines))


def _cmd_watch(chat_id, username, text):
    parts = text.split(maxsplit=1)
    addr = parts[1].strip() if len(parts) > 1 else ""
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
        send_message(
            chat_id,
            "Usage: `/watch 0x4f2a…c3a9`\n\n"
            "Paste the *full Hyperliquid wallet address* (0x + 40 characters).\n"
            "Works with *any* wallet — yours or any whale's.\n\n"
            "Included with your subscription (free trial: 7 days).",
        )
        return

    conn = _db()
    sub = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
    if not sub:
        conn.close()
        send_message(
            chat_id,
            "First link your account: open rustdeck.app → “Connect Telegram” → send me the code.",
        )
        return
    conn.execute("UPDATE subscribers SET watched_wallet=? WHERE chat_id=?", (addr, chat_id))
    conn.commit()
    conn.close()
    short = f"{addr[:10]}…{addr[-6:]}"
    send_message(
        chat_id,
        f"👀 *Now watching* `{short}`\n\n"
        f"You'll get a message here when the wallet:\n"
        f"• opens or closes a position\n"
        f"• gets liquidated\n"
        f"• executes any fill (limits, TP/SL)\n\n"
        f"Checks every minute, 24/7.\nStop: /unwatch",
    )


def _cmd_unwatch(chat_id):
    conn = _db()
    conn.execute("UPDATE subscribers SET watched_wallet=NULL WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()
    send_message(chat_id, "✅ Stopped watching. /watch 0x… to start again.")


def _cmd_watching(chat_id):
    conn = _db()
    sub = conn.execute(
        "SELECT watched_wallet FROM subscribers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    conn.close()
    if sub and sub["watched_wallet"]:
        a = sub["watched_wallet"]
        send_message(chat_id, f"👀 Watching `{a[:10]}…{a[-6:]}`\nStop: /unwatch")
    else:
        send_message(chat_id, "Not watching anything yet. /watch 0x… to start.")


def _cmd_status(chat_id):
    conn = _db()
    sub = conn.execute(
        "SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    conn.close()
    if not sub:
        send_message(
            chat_id,
            "📭 Your Telegram is not linked yet.\n"
            "Open rustdeck.app → “Connect Telegram” in the sidebar.",
        )
        return
    left_days = (sub["expires_at"] - time.time()) / 86400 if sub["expires_at"] else 0
    if left_days > 0:
        send_message(
            chat_id,
            f"💎 Plan: *{sub['tier']}*\n"
            f"Days left: *{max(0, int(left_days)) + 1}*\n\n"
            f"More tools: rustdeck.app",
        )
    else:
        send_message(
            chat_id,
            "⌛ Your free trial has ended.\n"
            "Paid plans are coming soon — for now everything stays free 🎁",
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
                "⚡ *RustDeck* — trading tools for crypto traders\n\n"
                "Wallet stats & market tools: rustdeck.app\n\n"
                "To link your Telegram: open the website → “Connect Telegram” → "
                "send me the code.\nOr tap the button below 👇",
                reply_markup={
                    "inline_keyboard": [[
                        {"text": "🎯 Open RustDeck", "url": "https://rustdeck.app"}
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
                "Format: `/link 123456`\n"
                "Generate the code on the website: rustdeck.app → “Connect Telegram”.",
            )
    elif text == "/prices":
        _cmd_prices(chat_id)
    elif text.startswith("/watch") and not text.startswith("/watching"):
        _cmd_watch(chat_id, username, text)
    elif text == "/unwatch":
        _cmd_unwatch(chat_id)
    elif text == "/watching":
        _cmd_watching(chat_id)
    elif text == "/status":
        _cmd_status(chat_id)
    elif text == "/help":
        send_message(
            chat_id,
            "*RustDeck — commands:*\n"
            "/prices — live prices of top-5 coins\n"
            "/watch 0x… — watch any Hyperliquid wallet 24/7\n"
            "  (paste the FULL wallet address; works with any wallet)\n"
            "/watching — what wallet is being watched\n"
            "/unwatch — stop watching\n"
            "/status — subscription status\n"
            "/link <code> — link your Telegram\n"
            "/start — start over\n\n"
            "🎯 Website: rustdeck.app",
        )
    else:
        send_message(chat_id, "I didn't get that 🤔 See /help for commands")


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
    threading.Thread(target=_watch_loop, daemon=True, name="rustdeck-tg-watch").start()
    return True

