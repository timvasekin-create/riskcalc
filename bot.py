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
  /top          — топ-5 трейдеров дня
  /alert BTC>90000 — ценовой алерт (и <)
  /alerts, /delalert N, /clearalerts — управление алертами
  /watch 0x…    — слежение за кошельками HL 24/7 (до 5 на аккаунт)
  /watching     — список отслеживаемых кошельков
  /unwatch [0x…]— остановить одного или всех
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
WATCH_LIMIT = 5         # максимум кошельков на аккаунт (мульти-/watch)
ALERT_LIMIT = 10        # максимум ценовых алертов на аккаунт (/alert)
WATCH_INTERVAL = 20     # секунд между опросами кошельков (короче — быстрее уведы)
SUBS_CHECK_INTERVAL = 3600   # раз в час: жёсткая проверка, что подписка ещё жива

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
        CREATE TABLE IF NOT EXISTS price_alerts (
            chat_id    INTEGER NOT NULL,
            sym        TEXT NOT NULL,
            op         TEXT NOT NULL,      -- '>' | '<'
            target     REAL NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS profiles (
            email      TEXT PRIMARY KEY,   -- Google-аккаунт с сайта
            name       TEXT,
            wallet     TEXT,               -- личный HL-кошелёк пользователя
            created_at REAL,
            updated_at REAL
        );
        """)
        # Миграции: колонки отслеживаемых кошельков и email (аккаунт с сайта)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(subscribers)").fetchall()]
        if "watched_wallet" not in cols:
            conn.execute("ALTER TABLE subscribers ADD COLUMN watched_wallet TEXT")
        if "email" not in cols:
            conn.execute("ALTER TABLE subscribers ADD COLUMN email TEXT")
        if "expired_notified" not in cols:
            conn.execute("ALTER TABLE subscribers ADD COLUMN expired_notified REAL")
        lcols = [r[1] for r in conn.execute("PRAGMA table_info(links)").fetchall()]
        if "email" not in lcols:
            conn.execute("ALTER TABLE links ADD COLUMN email TEXT")
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
    """Отправка сообщения (не бросает исключений — бот не должен ронять сайт).
    Если Telegram отклонил Markdown-разметку — повторяем без неё, чтобы
    уведомление всё равно дошло (иначе ошибка молча съедала сообщение)."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        _tg("sendMessage", payload)
        return True
    except Exception:
        pass
    payload.pop("parse_mode", None)
    try:
        _tg("sendMessage", payload)
        return True
    except Exception:
        return False


def _fmt_small(v):
    """Формат чисел без потери значащих цифр: 0.004362 (а не 0.0044), крупные — с запятыми."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    a = abs(v)
    if a == 0:
        return "0"
    if a >= 1000:
        return f"{v:,.2f}"
    return f"{v:g}"


def _side_label(side):
    """HL отдаёт сторону как A (ask/продажа) или B (bid/покупка) —
    в сообщениях показываем привычные Long/Short."""
    s = (side or "").strip().upper()
    if s in ("A", "ASK", "SELL", "S"):
        return "Short"
    if s in ("B", "BID", "BUY", "L"):
        return "Long"
    return side or "—"


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
def create_link_code(email=None):
    """Сайт генерирует 6-значный код, юзер отправляет его боту.
    email — аккаунт с сайта (по нему считаем «один триал на аккаунт»)."""
    email = (email or "").strip().lower() or None
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
        "INSERT INTO links (code, status, created_at, email) VALUES (?, 'pending', ?, ?)",
        (code, time.time(), email),
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
            "chat_id": row["chat_id"],
            "tg_username": sub["username"] if sub else None,
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
    # Свежие филлы кладём в снимок: цикл слежения не делает повторный запрос
    # (меньше вызовов HL → быстрее реакция и меньше шансов на rate limit)
    recent_fills = sorted(fills or [], key=lambda x: x.get("time") or 0, reverse=True)[:20]
    # Открытые ордера (лимитки, TP/SL) — для уведомлений о появлении/снятии
    orders = {}
    try:
        for o in _hl_post({"type": "frontendOpenOrders", "user": addr}) or []:
            k = f"{o.get('coin')}|{o.get('side')}|{o.get('limitPx') or o.get('triggerPx')}|{o.get('sz')}"
            orders[k] = {
                "coin": o.get("coin"),
                "side": o.get("side"),
                "side_label": _side_label(o.get("side")),
                "px": float(o.get("limitPx") or o.get("triggerPx") or 0),
                "sz": float(o.get("sz") or 0),
                "is_trigger": bool(o.get("isTrigger")),
            }
    except Exception:
        pass
    return {"positions": positions, "fill_keys": fill_keys, "orders": orders, "fills": recent_fills}


def _parse_watched(raw):
    """Строка 'addr1,addr2' -> валидный lowercase-список без дублей."""
    out = []
    for a in (raw or "").split(","):
        a = a.strip().lower()
        if re.fullmatch(r"0x[0-9a-f]{40}", a) and a not in out:
            out.append(a)
    return out


def add_watch(chat_id, addr):
    """Мост «сайт → Telegram»: хаб добавляет кошелёк в слежение бота.

    Возвращает {ok, count, limit, already} или {ok: False, error: ...}."""
    a = (addr or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", a):
        a = "0x" + a
    if not re.fullmatch(r"0x[0-9a-f]{40}", a):
        return {"ok": False, "error": "invalid_address"}
    if not BOT_TOKEN:
        return {"ok": False, "error": "bot_disabled"}
    conn = _db()
    sub = conn.execute(
        "SELECT watched_wallet FROM subscribers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    if not sub:
        conn.close()
        return {"ok": False, "error": "not_linked"}
    current = _parse_watched(sub["watched_wallet"])
    if a in current:
        conn.close()
        return {"ok": True, "already": True, "count": len(current), "limit": WATCH_LIMIT}
    if len(current) >= WATCH_LIMIT:
        conn.close()
        return {"ok": False, "error": "limit_reached", "limit": WATCH_LIMIT}
    current.append(a)
    conn.execute(
        "UPDATE subscribers SET watched_wallet=? WHERE chat_id=?",
        (",".join(current), chat_id),
    )
    conn.commit()
    conn.close()
    send_message(
        chat_id,
        f"👀 *Website:* now watching `{a[:10]}…{a[-6:]}` in Telegram too ({len(current)}/{WATCH_LIMIT}).\n"
        "Alerts arrive here automatically.",
    )
    return {"ok": True, "count": len(current), "limit": WATCH_LIMIT}


def subscriber_by_email(email):
    """Аккаунт сайта (email) → привязанный Telegram (для /api/me на хабе)."""
    email = (email or "").strip().lower()
    if not email:
        return None
    conn = _db()
    row = conn.execute(
        "SELECT chat_id, username, tier, expires_at FROM subscribers "
        "WHERE email=? ORDER BY linked_at DESC LIMIT 1",
        (email,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "chat_id": row["chat_id"],
        "username": row["username"],
        "tier": row["tier"],
        "expires_at": row["expires_at"],
    }


# ============================================================
# Подписка: жёсткая проверка + профили сайта (Google-аккаунт)
# ============================================================
def subscription_days_left(sub):
    """Сколько дней осталось (None, если подписки нет вообще)."""
    if not sub:
        return None
    try:
        exp = sub["expires_at"]
    except (KeyError, IndexError, TypeError):
        exp = None
    if not exp:
        return None
    return max(0, int((exp - time.time()) / 86400) + 1)


def subscription_active(sub):
    """Подписка живёт ровно пока expires_at > now — без исключений."""
    if not sub:
        return False
    try:
        exp = sub["expires_at"]
    except (KeyError, IndexError, TypeError):
        exp = None
    return bool(exp and exp > time.time())


def get_profile(email):
    """Профиль сайта из БД (имя + личный кошелёк)."""
    email = (email or "").strip().lower()
    if not email:
        return None
    conn = _db()
    row = conn.execute("SELECT * FROM profiles WHERE email=?", (email,)).fetchone()
    conn.close()
    if not row:
        return {"email": email, "name": None, "wallet": None}
    return {"email": row["email"], "name": row["name"], "wallet": row["wallet"]}


def save_profile(email, name=None, wallet=None):
    """Профиль сайта (Google-аккаунт): имя и личный кошелёк.
    Пустые значения = «не менять»."""
    email = (email or "").strip().lower()
    if not email:
        return None
    conn = _db()
    row = conn.execute("SELECT * FROM profiles WHERE email=?", (email,)).fetchone()
    now = time.time()
    new_name = (name or "").strip()[:24] or (row["name"] if row else None)
    new_wallet = (wallet or "").strip().lower() or (row["wallet"] if row else None)
    if row:
        conn.execute(
            "UPDATE profiles SET name=?, wallet=?, updated_at=? WHERE email=?",
            (new_name, new_wallet, now, email),
        )
    else:
        conn.execute(
            "INSERT INTO profiles (email, name, wallet, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (email, new_name, new_wallet, now, now),
        )
    conn.commit()
    conn.close()
    return {"email": email, "name": new_name, "wallet": new_wallet}


def user_status(email):
    """Полный статус аккаунта: профиль сайта + TG-подписка (для /api/me)."""
    email = (email or "").strip().lower()
    prof = get_profile(email) or {"email": email, "name": None, "wallet": None}
    sub = subscriber_by_email(email)
    tg = None
    if sub:
        tg = {
            "chat_id": sub["chat_id"],
            "username": sub["username"],
            "tier": sub["tier"],
            "expires_at": sub["expires_at"],
            "days_left": subscription_days_left(sub),
            "active": subscription_active(sub),
        }
    return {"email": email, "name": prof["name"], "wallet": prof["wallet"], "tg": tg}


def list_users():
    """Для админ-панели: все аккаунты (site-профили + TG-подписки)."""
    conn = _db()
    profs = {r["email"]: dict(r) for r in conn.execute("SELECT * FROM profiles").fetchall()}
    subs = [dict(r) for r in conn.execute("SELECT * FROM subscribers").fetchall()]
    alerts = {r["chat_id"]: r["n"] for r in conn.execute(
        "SELECT chat_id, COUNT(*) AS n FROM price_alerts GROUP BY chat_id").fetchall()}
    conn.close()
    users, used = [], set()
    for s in subs:
        email = (s.get("email") or "").strip().lower()
        p = profs.get(email) or {}
        used.add(email)
        users.append({
            "email": email or None,
            "name": p.get("name") or s.get("username") or None,
            "wallet": p.get("wallet") or None,
            "tg_username": s.get("username"),
            "chat_id": s.get("chat_id"),
            "tier": s.get("tier"),
            "expires_at": s.get("expires_at"),
            "days_left": subscription_days_left(s),
            "active": subscription_active(s),
            "wallets": len(_parse_watched(s.get("watched_wallet"))),
            "alerts": alerts.get(s.get("chat_id"), 0),
            "linked_at": s.get("linked_at"),
        })
    for email, p in profs.items():
        if not email or email in used:
            continue
        users.append({
            "email": email, "name": p.get("name"), "wallet": p.get("wallet"),
            "tg_username": None, "chat_id": None, "tier": None, "expires_at": None,
            "days_left": None, "active": False, "wallets": 0, "alerts": 0,
            "linked_at": p.get("created_at"),
        })
    users.sort(key=lambda u: (not u["active"], -(u["expires_at"] or 0)))
    return users


def extend_subscription(chat_id=None, email=None, days=7):
    """Админ добавляет дни к подписке (от максимума из now/текущего срока).
    Подписка привязана к TG-аккаунту: без chat_id продлевать нечего."""
    try:
        days = max(1, min(int(days or 0), 365))
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_days"}
    conn = _db()
    row = None
    if chat_id:
        row = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
    if row is None and email:
        row = conn.execute(
            "SELECT * FROM subscribers WHERE email=? ORDER BY linked_at DESC LIMIT 1",
            ((email or "").strip().lower(),),
        ).fetchone()
    if row is None:
        conn.close()
        return {"ok": False, "error": "not_linked"}
    base = max(time.time(), row["expires_at"] or 0)
    new_exp = base + days * 86400
    conn.execute(
        "UPDATE subscribers SET expires_at=?, tier='pro', expired_notified=NULL WHERE chat_id=?",
        (new_exp, row["chat_id"]),
    )
    conn.commit()
    conn.close()
    send_message(
        row["chat_id"],
        f"🎁 *Access extended* by {days} day{'s' if days > 1 else ''}.\n"
        "Alerts are active again — happy trading!",
    )
    return {
        "ok": True,
        "chat_id": row["chat_id"],
        "tier": "pro",
        "expires_at": new_exp,
        "days_left": subscription_days_left({"expires_at": new_exp}),
    }


def _subs_loop():
    """Жёсткая проверка подписки: раз в час. Истёкшим — стоп алертов и одно
    уведомление в TG; после продления всё возвращается автоматически."""
    while True:
        try:
            now = time.time()
            conn = _db()
            rows = conn.execute(
                "SELECT chat_id FROM subscribers "
                "WHERE expires_at IS NOT NULL AND expires_at <= ? AND expired_notified IS NULL",
                (now,),
            ).fetchall()
            conn.close()
            for row in rows:
                conn = _db()
                conn.execute("UPDATE subscribers SET expired_notified=? WHERE chat_id=?", (now, row["chat_id"]))
                conn.commit()
                conn.close()
                send_message(
                    row["chat_id"],
                    "⌛ *Your subscription has ended* — alerts and commands are paused.\n"
                    "Ask the RustDeck team to add days: everything resumes instantly.",
                )
        except Exception:
            pass
        time.sleep(SUBS_CHECK_INTERVAL)


def _watch_loop():
    """Фоновый цикл: раз в 60с опрашивает отслеживаемые кошельки подписчиков."""
    cache = {}  # addr -> snapshot
    while True:
        try:
            conn = _db()
            rows = conn.execute(
                "SELECT chat_id, watched_wallet, expires_at FROM subscribers WHERE watched_wallet IS NOT NULL"
            ).fetchall()
            conn.close()

            # addr -> {chat_id, ...}: дедупликация — один опрос HL на адрес,
            # даже если за кошельком следят несколько юзеров
            subs_map = {}
            for row in rows:
                if not subscription_active(row):
                    continue  # подписка кончилась — алерты не шлём (ждёт продления)
                for a in _parse_watched(row["watched_wallet"]):
                    subs_map.setdefault(a, set()).add(row["chat_id"])
            # чистим кэш по адресам, которые больше никто не отслеживает
            cache = {a: s for a, s in cache.items() if a in subs_map}

            for addr, chat_ids in subs_map.items():
                try:
                    snap = _wallet_snapshot(addr)
                except Exception:
                    continue
                prev = cache.get(addr)
                cache[addr] = snap
                if prev is None:
                    continue  # первый опрос — фиксируем базу

                events = []
                fills = snap.get("fills") or []
                for f in sorted(fills, key=lambda x: x.get("time") or 0, reverse=True)[:20]:
                    key = f"{f.get('time')}|{f.get('coin')}|{f.get('side')}|{f.get('px')}|{f.get('sz')}"
                    if key in prev["fill_keys"]:
                        continue
                    try:
                        pnl = float(f.get("closedPnl") or 0)
                        px = float(f.get("px") or 0)
                        sz = float(f.get("sz") or 0)
                    except (TypeError, ValueError):
                        continue
                    dir_s = f.get("dir") or _side_label(f.get("side"))
                    if "iquidat" in dir_s:
                        events.append(f"💥 *LIQUIDATION:* {f.get('coin')} {dir_s} @ ${_fmt_small(px)} · PnL ${pnl:+,.2f}")
                    elif pnl != 0:
                        emoji = "🟢" if pnl > 0 else "🔴"
                        events.append(f"{emoji} *Position closed:* {f.get('coin')} {dir_s} @ ${_fmt_small(px)} · PnL ${pnl:+,.2f}")
                    elif "open" in dir_s.lower():
                        events.append(f"🟢 *Position opened:* {f.get('coin')} {dir_s} @ ${_fmt_small(px)} · size {_fmt_small(sz)}")
                    else:
                        events.append(f"🔔 *Fill:* {f.get('coin')} {dir_s} @ ${_fmt_small(px)} · size {_fmt_small(sz)}")

                prev_pos, now_pos = prev["positions"], snap["positions"]
                for c in now_pos:
                    if c not in prev_pos and not any("opened" in e for e in events):
                        p = now_pos[c]
                        events.append(f"🟢 *Position opened:* {c} {p['side']} · ${p['size_usd']:,.0f} @ ${_fmt_small(p['entry'])}")
                for c in prev_pos:
                    if c not in now_pos and not any("closed" in e or "LIQUIDATION" in e for e in events):
                        events.append(f"🔒 *Position closed:* {c}")

                # Лимитки / TP / SL: появление и снятие ордеров
                prev_o, now_o = prev.get("orders", {}), snap.get("orders", {})
                for k, o in now_o.items():
                    if k in prev_o or any(o["coin"] in e for e in events):
                        continue
                    kind = "Stop/TP order" if o["is_trigger"] else "Limit order"
                    side_s = o.get("side_label") or o["side"]
                    events.append(f"🧾 *{kind}:* {o['coin']} {side_s} · {_fmt_small(o['sz'])} @ ${_fmt_small(o['px'])}")
                for k, o in prev_o.items():
                    if k in now_o or any(o["coin"] in e for e in events):
                        continue
                    side_s = o.get("side_label") or o["side"]
                    events.append(f"🗑 *Order removed:* {o['coin']} {side_s} @ ${_fmt_small(o['px'])}")

                for ev in events:
                    for cid in chat_ids:
                        send_message(cid, ev)

            # Ценовые алерты: один запрос цен на всех юзеров
            try:
                conn = _db()
                pa = conn.execute("SELECT rowid, chat_id, sym, op, target FROM price_alerts").fetchall()
                active_ids = {r["chat_id"] for r in conn.execute(
                    "SELECT chat_id FROM subscribers WHERE expires_at IS NOT NULL AND expires_at > ?",
                    (time.time(),)).fetchall()}
                conn.close()
                if pa:
                    prices = _hl_prices({a["sym"] for a in pa if a["chat_id"] in active_ids})
                    for a in pa:
                        if a["chat_id"] not in active_ids:
                            continue  # подписка истекла — алерт не отправляем
                        p = prices.get(a["sym"])
                        if p is None:
                            continue
                        hit = (a["op"] == ">" and p >= a["target"]) or (a["op"] == "<" and p <= a["target"])
                        if not hit:
                            continue
                        op_txt = "above" if a["op"] == ">" else "below"
                        send_message(
                            a["chat_id"],
                            f"🔔 *Price alert:* {a['sym']} crossed {op_txt} ${_fmt_small(a['target'])}\n"
                            f"Now: *${_fmt_small(p)}*",
                        )
                        conn = _db()
                        conn.execute("DELETE FROM price_alerts WHERE rowid=?", (a["rowid"],))
                        conn.commit()
                        conn.close()
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(WATCH_INTERVAL)


# ============================================================
# Команды бота
# ============================================================
def _try_link(chat_id, username, code):
    """Юзер отправил код боту — связываем аккаунты.
    Триал: один на Telegram-аккаунт И один на email (аккаунт сайта)."""
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

    email = None
    try:
        email = (row["email"] or "").strip().lower() or None
    except (IndexError, KeyError):
        pass

    expires = time.time() + TRIAL_DAYS * 86400
    conn.execute(
        "UPDATE links SET status='linked', chat_id=? WHERE code=?", (chat_id, code)
    )
    existing = conn.execute(
        "SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    # Триал по email: если этот email уже получал триал на другом чате — второй не даём
    email_used = False
    if email:
        email_used = conn.execute(
            "SELECT 1 FROM subscribers WHERE email=? AND chat_id<>? LIMIT 1",
            (email, chat_id),
        ).fetchone() is not None

    if existing is None:
        if email_used:
            conn.execute(
                """INSERT INTO subscribers (chat_id, username, tier, expires_at, linked_at, email)
                   VALUES (?, ?, 'free', ?, ?, ?)""",
                (chat_id, username, time.time(), time.time(), email),
            )
            message = (
                "✅ *Telegram linked to RustDeck!*\n\n"
                "ℹ️ This email has already used the free trial on another Telegram account, "
                "so the trial is not granted again.\n"
                "Paid plans are coming soon — thanks for testing!"
            )
        else:
            conn.execute(
                """INSERT INTO subscribers (chat_id, username, tier, expires_at, linked_at, email)
                   VALUES (?, ?, 'trial', ?, ?, ?)""",
                (chat_id, username, expires, time.time(), email),
            )
            until = time.strftime("%b %d, %Y", time.gmtime(expires))
            message = (
                "✅ *Telegram linked to RustDeck!*\n\n"
                + (f"📧 Account: *{email}*\n" if email else "")
                + f"🎁 Free trial: *{TRIAL_DAYS} days* (until {until})\n\n"
                "👀 Watch up to 5 wallets 24/7: send /watch 0x…\n"
                "(paste the FULL Hyperliquid address — works with any wallet)\n\n"
                "Commands: /prices — live prices, /status — subscription, /help — all."
            )
    else:
        if email:
            conn.execute("UPDATE subscribers SET email=? WHERE chat_id=?", (email, chat_id))
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


def _cmd_top(chat_id):
    """Топ-5 трейдеров дня из официального лидерборда Hyperliquid."""
    try:
        req = urllib.request.Request(
            "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
            headers={"User-Agent": "rustdeck-bot/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        send_message(chat_id, "⚠️ Leaderboard is temporarily unavailable. Try again later.")
        return

    rows = data.get("leaderboardRows") or []
    traders = []
    for r in rows:
        try:
            val = float(r.get("accountValue") or 0)
        except (TypeError, ValueError):
            continue
        if val < 10_000:
            continue
        for pair in r.get("windowPerformances") or []:
            if isinstance(pair, (list, tuple)) and pair[0] == "day" and isinstance(pair[1], dict):
                try:
                    pnl = float(pair[1].get("pnl") or 0)
                except (TypeError, ValueError):
                    continue
                if pnl > 0:
                    traders.append((pnl, r.get("ethAddress") or "", r.get("displayName") or ""))
                break
    traders.sort(reverse=True)
    top = traders[:5]
    if not top:
        send_message(chat_id, "⚠️ No data yet. Try again later.")
        return

    lines = ["🏆 *Top-5 Traders Today (Hyperliquid)*\n"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, (pnl, addr, name) in enumerate(top):
        label = name if name else f"{addr[:6]}…{addr[-4:]}"
        lines.append(f"{medals[i]} `{addr}`\n    {label} · *+${pnl:,.0f}*")
    lines.append("\nTrack any of them: /watch 0x…\nFull stats: rustdeck.app")
    send_message(chat_id, "\n".join(lines))


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


def _hl_prices(syms=None):
    """Цены перпов Hyperliquid (allMids): {SYM: float} или только нужные символы."""
    try:
        mids = _hl_post({"type": "allMids"}) or {}
    except Exception:
        return {}
    out = {}
    for k, v in mids.items():
        try:
            out[k.upper()] = float(v)
        except (TypeError, ValueError):
            continue
    if syms:
        return {s: out[s] for s in syms if s in out}
    return out


def _cmd_alert(chat_id, username, text):
    """/alert BTC > 90000 — уведомление при пересечении цены."""
    m = re.fullmatch(
        r"/alert\s+([A-Za-z0-9@][A-Za-z0-9@\-]{0,11})\s*([<>])\s*([0-9]*\.?[0-9]+)",
        (text or "").strip(),
        re.IGNORECASE,
    )
    if not m:
        send_message(chat_id, "Usage: `/alert BTC > 90000` or `/alert ETH < 2500`\nSee all: /alerts")
        return
    sym, op, target = m.group(1).upper(), m.group(2), float(m.group(3))
    conn = _db()
    sub = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
    if not sub:
        conn.close()
        send_message(chat_id, "First link your account: sign in on rustdeck.app → “Create link code” → send me `/link <code>`.")
        return
    if not subscription_active(sub):
        conn.close()
        send_message(
            chat_id,
            "⌛ *Your subscription has ended* — alerts are paused.\n"
            "Ask the RustDeck team to add days: everything resumes instantly.",
        )
        return
    cnt = conn.execute("SELECT COUNT(*) FROM price_alerts WHERE chat_id=?", (chat_id,)).fetchone()[0]
    if cnt >= ALERT_LIMIT:
        conn.close()
        send_message(chat_id, f"⚠️ Alert limit reached ({ALERT_LIMIT}). Remove one: `/delalert 1`")
        return
    conn.close()
    price = _hl_prices({sym}).get(sym)
    if price is None:
        send_message(chat_id, f"Unknown asset `{sym}`. Use a Hyperliquid perp ticker, e.g. BTC, ETH, SOL, HYPE.")
        return
    conn = _db()
    conn.execute(
        "INSERT INTO price_alerts (chat_id, sym, op, target, created_at) VALUES (?, ?, ?, ?, ?)",
        (chat_id, sym, op, target, time.time()),
    )
    conn.commit()
    conn.close()
    op_txt = "above" if op == ">" else "below"
    send_message(
        chat_id,
        f"🔔 *Alert set:* {sym} goes {op_txt} ${_fmt_small(target)}\n"
        f"Current price: ${_fmt_small(price)}\n\nSee all: /alerts",
    )


def _cmd_alerts(chat_id):
    conn = _db()
    rows = conn.execute(
        "SELECT sym, op, target FROM price_alerts WHERE chat_id=? ORDER BY created_at", (chat_id,)
    ).fetchall()
    conn.close()
    if not rows:
        send_message(chat_id, "No price alerts yet. Example: `/alert BTC > 90000`")
        return
    prices = _hl_prices({r["sym"] for r in rows})
    lines = ["🔔 *Your price alerts:*"]
    for i, r in enumerate(rows, 1):
        cur = prices.get(r["sym"])
        cur_s = f" → now ${_fmt_small(cur)}" if cur is not None else ""
        lines.append(f"{i}. {r['sym']} {r['op']} ${_fmt_small(r['target'])}{cur_s}")
    lines.append("\nRemove: `/delalert N` · All: /clearalerts")
    send_message(chat_id, "\n".join(lines))


def _cmd_delalert(chat_id, text):
    m = re.fullmatch(r"/(?:delete|del)alert\s+(\d+)", (text or "").strip(), re.IGNORECASE)
    if not m:
        send_message(chat_id, "Usage: `/delalert 1` (number from /alerts)")
        return
    n = int(m.group(1))
    conn = _db()
    rows = conn.execute(
        "SELECT rowid FROM price_alerts WHERE chat_id=? ORDER BY created_at", (chat_id,)
    ).fetchall()
    if n < 1 or n > len(rows):
        conn.close()
        send_message(chat_id, "No such alert — check /alerts")
        return
    conn.execute("DELETE FROM price_alerts WHERE rowid=?", (rows[n - 1]["rowid"],))
    conn.commit()
    conn.close()
    send_message(chat_id, f"✅ Alert #{n} removed.")


def _cmd_clearalerts(chat_id):
    conn = _db()
    conn.execute("DELETE FROM price_alerts WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()
    send_message(chat_id, "✅ All price alerts removed.")


def _cmd_watch(chat_id, username, text):
    parts = text.split(maxsplit=1)
    addr = parts[1].strip() if len(parts) > 1 else ""
    # Терпимо к формату: без 0x, в любом регистре, с лишними пробелами
    addr = addr.lower()
    if re.fullmatch(r"[0-9a-f]{40}", addr):
        addr = "0x" + addr
    if not re.fullmatch(r"0x[0-9a-f]{40}", addr):
        send_message(
            chat_id,
            "Usage: `/watch 0x4f2a…c3a9`\n\n"
            "Paste the *full Hyperliquid wallet address* (0x + 40 characters).\n"
            "Or just send the address as a message — I'll get it.\n"
            "Works with *any* wallet — yours or any whale's.\n"
            f"Up to {WATCH_LIMIT} wallets per account.\n\n"
            "Included with your subscription (free trial: 7 days).",
        )
        return

    conn = _db()
    sub = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
    if not sub:
        conn.close()
        send_message(
            chat_id,
            "First link your account: sign in on rustdeck.app → “Create link code” → send me `/link <code>`.",
        )
        return
    if not subscription_active(sub):
        conn.close()
        send_message(
            chat_id,
            "⌛ *Your subscription has ended* — watching is paused.\n"
            "Ask the RustDeck team to add days: your wallets and alerts resume instantly.",
        )
        return
    current = _parse_watched(sub["watched_wallet"])
    short = f"{addr[:10]}…{addr[-6:]}"
    if addr in current:
        conn.close()
        send_message(chat_id, f"👀 Already watching `{short}`\n\nSee all: /watching")
        return
    if len(current) >= WATCH_LIMIT:
        conn.close()
        send_message(
            chat_id,
            f"⚠️ Watch limit reached ({WATCH_LIMIT} wallets).\n"
            "Remove one first: `/unwatch 0x…`\n"
            "See the list: /watching",
        )
        return
    current.append(addr)
    conn.execute("UPDATE subscribers SET watched_wallet=? WHERE chat_id=?", (",".join(current), chat_id))
    conn.commit()
    conn.close()
    lines = [f"👀 *Now watching ({len(current)}/{WATCH_LIMIT}):*"]
    lines += [f"• `{a[:10]}…{a[-6:]}`" for a in current]
    lines.append(
        "\nYou'll get a message here when a wallet:\n"
        "• opens or closes a position\n"
        "• gets liquidated\n"
        "• executes any fill (limits, TP/SL)\n\n"
        f"Checks every {WATCH_INTERVAL} seconds, 24/7.\n"
        "See all: /watching · Stop one: `/unwatch 0x…` · Stop all: /unwatch"
    )
    send_message(chat_id, "\n".join(lines))


def _cmd_unwatch(chat_id, text=""):
    """Без аргумента — стоп всем кошелькам. С адресом — убрать только его."""
    parts = (text or "").split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    if re.fullmatch(r"[0-9a-f]{40}", arg):
        arg = "0x" + arg

    conn = _db()
    sub = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
    current = _parse_watched(sub["watched_wallet"]) if sub else []

    if arg and re.fullmatch(r"0x[0-9a-f]{40}", arg):
        if arg not in current:
            conn.close()
            send_message(chat_id, f"Not watching `{arg[:10]}…{arg[-6:]}`. List: /watching")
            return
        current.remove(arg)
        conn.execute(
            "UPDATE subscribers SET watched_wallet=? WHERE chat_id=?",
            (",".join(current) or None, chat_id),
        )
        conn.commit()
        conn.close()
        extra = f"\nStill watching {len(current)} — /watching" if current else ""
        send_message(chat_id, f"✅ Stopped watching `{arg[:10]}…{arg[-6:]}`{extra}")
        return

    conn.execute("UPDATE subscribers SET watched_wallet=NULL WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()
    if current:
        send_message(chat_id, f"✅ Stopped watching all ({len(current)}). /watch 0x… to start again.")
    else:
        send_message(chat_id, "You weren't watching anything. /watch 0x… to start.")


def _cmd_watching(chat_id):
    conn = _db()
    sub = conn.execute(
        "SELECT watched_wallet FROM subscribers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    conn.close()
    addrs = _parse_watched(sub["watched_wallet"]) if sub else []
    if not addrs:
        send_message(chat_id, "Not watching anything yet. /watch 0x… to start.")
        return
    lines = [f"👀 *Watching ({len(addrs)}/{WATCH_LIMIT}):*"]
    for i, a in enumerate(addrs, 1):
        lines.append(f"{i}. `{a[:10]}…{a[-6:]}`")
    lines.append("\nStats: rustdeck.app\nRemove one: `/unwatch 0x…` · Stop all: /unwatch")
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
            "📭 Your Telegram is not linked yet.\n"
            "Sign in on rustdeck.app → “Create link code” → send /link <code>.",
        )
        return
    left_days = (sub["expires_at"] - time.time()) / 86400 if sub["expires_at"] else 0
    try:
        email = sub["email"]
    except (IndexError, KeyError):
        email = None
    email_line = f"📧 Account: *{email}*\n" if email else ""
    if left_days > 0:
        send_message(
            chat_id,
            f"💎 Plan: *{sub['tier']}*\n"
            f"{email_line}"
            f"Days left: *{max(0, int(left_days)) + 1}*\n\n"
            f"More tools: rustdeck.app",
        )
    else:
        send_message(
            chat_id,
            f"⌛ Your free trial has ended.\n"
            f"{email_line}"
            "Paid plans are coming soon — for now everything stays free 🎁",
        )


def _send_welcome(chat_id):
    """Приветствие /start без целевого payload."""
    send_message(
        chat_id,
        "⚡ *RustDeck* — trading tools for crypto traders\n\n"
        "Wallet stats & market tools: rustdeck.app\n\n"
        "To link your Telegram: sign in on the website → "
        "“Create link code” → send me `/link <code>`.\nOr tap the button below 👇",
        reply_markup={
            "inline_keyboard": [[
                {"text": "🎯 Open RustDeck", "url": "https://rustdeck.app"}
            ]]
        },
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
        elif payload.lower().startswith("watch"):
            # Deep-link из Whale Feed / лидерборда: /start watch_0x…
            addr = payload[5:].strip().lower().lstrip("_")
            if re.fullmatch(r"[0-9a-f]{40}", addr):
                addr = "0x" + addr
            if re.fullmatch(r"0x[0-9a-f]{40}", addr):
                send_message(chat_id, f"👀 Following `{addr[:10]}…{addr[-6:]}`")
                _cmd_watch(chat_id, username, "/watch " + addr)
            else:
                _send_welcome(chat_id)
        else:
            _send_welcome(chat_id)
    elif text.startswith("/link"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        if re.fullmatch(r"\d{6}", code):
            _try_link(chat_id, username, code)
        else:
            send_message(
                chat_id,
                "Format: `/link 123456`\n"
                "Get the code on the website: rustdeck.app → Sign in → “Create link code”.",
            )
    elif text == "/prices":
        _cmd_prices(chat_id)
    elif text == "/top":
        _cmd_top(chat_id)
    elif text == "/alerts":
        _cmd_alerts(chat_id)
    elif text == "/clearalerts":
        _cmd_clearalerts(chat_id)
    elif text.startswith("/delalert") or text.startswith("/deletealert"):
        _cmd_delalert(chat_id, text)
    elif text.startswith("/alert"):
        _cmd_alert(chat_id, username, text)
    elif text.startswith("/watch") and not text.startswith("/watching"):
        _cmd_watch(chat_id, username, text)
    elif text.startswith("/unwatch"):
        _cmd_unwatch(chat_id, text)
    elif text == "/watching":
        _cmd_watching(chat_id)
    elif re.fullmatch(r"0x[0-9a-f]{40}", text.lower()) or re.fullmatch(r"[0-9a-f]{40}", text.lower()):
        # Голый адрес кошелька (без команды) — сразу начинаем слежение
        _cmd_watch(chat_id, username, "/watch " + text)
    elif text == "/status":
        _cmd_status(chat_id)
    elif text == "/help":
        send_message(
            chat_id,
            "*RustDeck — commands:*\n"
            "/prices — live prices of top-5 coins\n"
            "/top — top-5 traders today (PnL)\n"
            "/alert BTC > 90000 — price alert (also <)\n"
            "/alerts — your price alerts\n"
            "/delalert N · /clearalerts — remove alerts\n"
            "/watch 0x… — watch any Hyperliquid wallet 24/7\n"
            "  (paste the FULL wallet address; up to 5 wallets)\n"
            "/watching — list your watched wallets\n"
            "/unwatch [0x…] — stop one or all wallets\n"
            "/status — subscription status\n"
            "/link <code> — link your Telegram\n"
            "  (code: rustdeck.app → Sign in → Create link code)\n"
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
    threading.Thread(target=_subs_loop, daemon=True, name="rustdeck-tg-subs").start()
    return True

