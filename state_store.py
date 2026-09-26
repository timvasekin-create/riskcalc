# -*- coding: utf-8 -*-
"""Внешнее хранение состояния (SQLite) — из-за Render Free «сбрасывались»
отслеживаемые кошельки и подписки: у бесплатного плана диск эфемерный, и
каждый деплой/спин-даун создаёт контейнер с пустой базой.

Как работает:
  * раз в ~20 секунд, если база изменилась, и раз в 10 минут (heartbeat)
    консистентный снимок базы (sqlite backup API + gzip) уезжает
    в ПРИВАТНЫЙ GitHub Gist;
  * на старте, если локальная база пустая (свежий контейнер), она
    восстанавливается из гиста.

Настройка (бесплатно, ~2 минуты):
  1) GitHub → Settings → Developer settings → Personal access tokens →
     токен с правом `gist` (classic) или "Gists: Read and write" (fine-grained);
  2) Render → сервис rustdeck-calc → Environment →
     STATE_GITHUB_TOKEN = <токен>  (GIST_ID указывать не нужно — гист
     создастся сам, приватный, с описанием 'rustdeck-state').

Зависимостей нет: urllib + gzip + base64 + sqlite3 из стандартной библиотеки.
"""
import base64
import gzip
import json
import os
import sqlite3
import tempfile
import threading
import time
import urllib.request

TOKEN = os.environ.get("STATE_GITHUB_TOKEN", "").strip()
GIST_ID = os.environ.get("STATE_GIST_ID", "").strip()
GIST_DESC = "rustdeck-state"
FILE_NAME = "rustdeck.db.gz.b64"
API = "https://api.github.com"
CHECK_INTERVAL = 20     # как часто проверяем, изменилась ли база
HEARTBEAT = 600         # даже без изменений — снимок раз в 10 минут

_lock = threading.Lock()
_state = {
    "configured": bool(TOKEN),
    "gist_id": GIST_ID or None,
    "last_push": 0.0,
    "last_pull": 0.0,
    "last_error": None,
    "pushed_bytes": 0,
    "started": False,
}


def configured() -> bool:
    return bool(TOKEN)


def status() -> dict:
    """Для админки: включён ли бэкап, когда был последний обмен."""
    with _lock:
        return dict(_state)


def _note_error(exc):
    with _lock:
        _state["last_error"] = f"{type(exc).__name__}: {exc}"[:200]


# ===== GitHub API =====
def _api(method, path, payload=None, timeout=25):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "rustdeck-state/1.0",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def _ensure_gist() -> str:
    """ID гиста с состоянием: из env, из списка гистов или создаём новый."""
    with _lock:
        if _state["gist_id"]:
            return _state["gist_id"]
    gid = None
    for page in (1, 2, 3):
        gists = _api("GET", f"/gists?per_page=100&page={page}") or []
        for g in gists:
            if (g.get("description") or "") == GIST_DESC or FILE_NAME in (g.get("files") or {}):
                gid = g.get("id")
                break
        if gid or len(gists) < 100:
            break
    if not gid:
        created = _api("POST", "/gists", {
            "description": GIST_DESC,
            "public": False,
            "files": {FILE_NAME: {"content": ""}},
        })
        gid = created.get("id")
    if not gid:
        raise RuntimeError("не удалось создать/найти gist")
    with _lock:
        _state["gist_id"] = gid
    return gid


# ===== Снимок и восстановление =====
def snapshot_bytes(db_path: str) -> bytes:
    """Консистентный снимок SQLite (backup API) → gzip. b'' — если базы нет."""
    if not os.path.exists(db_path):
        return b""
    fd, tmp_path = tempfile.mkstemp(suffix=".dbstate")
    os.close(fd)
    raw = b""
    try:
        src = sqlite3.connect(db_path, timeout=15)
        dest = sqlite3.connect(tmp_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
            src.close()
        with open(tmp_path, "rb") as fh:
            raw = fh.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return gzip.compress(raw, 6) if raw else b""


def db_is_empty(db_path: str) -> bool:
    """Пустая база = свежий контейнер (нужно тянуть снимок)."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) < 2048:
        return True
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            for table in ("subscribers", "profiles", "links"):
                try:
                    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.Error:
                    return True     # таблиц нет — база пустая/битая
                if n:
                    return False
        finally:
            conn.close()
    except Exception:
        return False
    return True


def restore_if_empty(db_path: str) -> bool:
    """Тянет последний снимок из гиста, если локальная база пустая."""
    if not TOKEN:
        return False
    try:
        if not db_is_empty(db_path):
            return False
    except Exception:
        return False
    try:
        gid = _ensure_gist()
        info = _api("GET", f"/gists/{gid}")
        f = (info.get("files") or {}).get(FILE_NAME) or {}
        content = f.get("content") or ""
        if f.get("truncated") and f.get("raw_url"):
            with urllib.request.urlopen(f["raw_url"], timeout=30) as r:
                content = r.read().decode("utf-8")
        if not content.strip():
            return False    # пустой гист — восстанавливать нечего
        raw = gzip.decompress(base64.b64decode(content))
        if not raw.startswith(b"SQLite format 3"):
            raise ValueError("снимок не похож на SQLite")
        tmp = db_path + ".restore"
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, db_path)
        with _lock:
            _state.update(last_pull=time.time(), last_error=None)
        return True
    except Exception as e:
        _note_error(e)
        return False


def push(db_path: str, force: bool = False) -> bool:
    """Отправляет снимок базы в gist (не чаще CHECK_INTERVAL без force)."""
    if not TOKEN:
        return False
    now = time.time()
    with _lock:
        if not force and now - _state["last_push"] < CHECK_INTERVAL:
            return False
    try:
        blob = snapshot_bytes(db_path)
        if not blob:
            return False
        gid = _ensure_gist()
        _api("PATCH", f"/gists/{gid}", {
            "description": GIST_DESC,
            "files": {FILE_NAME: {"content": base64.b64encode(blob).decode("ascii")}},
        })
        with _lock:
            _state.update(last_push=time.time(), pushed_bytes=len(blob), last_error=None)
        return True
    except Exception as e:
        _note_error(e)
        return False


def _signature(db_path: str):
    try:
        st = os.stat(db_path)
        return (st.st_size, int(st.st_mtime))
    except OSError:
        return None


def start_sync_loop(db_path: str) -> bool:
    """Фоновый поток синхронизации (демон). True, если бэкап настроен."""
    if not TOKEN:
        return False
    with _lock:
        if _state["started"]:
            return True
        _state["started"] = True

    def loop():
        last_sig = None
        last_beat = 0.0
        streak = 0
        while True:
            try:
                sig = _signature(db_path)
                now = time.time()
                if sig and (sig != last_sig or now - last_beat > HEARTBEAT):
                    if push(db_path, force=True):
                        last_sig, last_beat, streak = sig, now, 0
                    else:
                        # Ошибка (например, неверный токен): тормозим с ростом
                        # паузы, чтобы не долбить GitHub и не спамить логи
                        streak = min(streak + 1, 6)
            except Exception as e:
                _note_error(e)
                streak = min(streak + 1, 6)
            time.sleep(CHECK_INTERVAL * (2 ** streak) if streak else CHECK_INTERVAL)

    threading.Thread(target=loop, daemon=True).start()
    return True
