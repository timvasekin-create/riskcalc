# -*- coding: utf-8 -*-
# Тест бота с реальным токеном: BOT_TOKEN берём из окружения (передаётся при запуске)
import subprocess, sys, time, json, urllib.request, os
import base64, hashlib, hmac

assert os.environ.get("BOT_TOKEN"), "BOT_TOKEN не задан в окружении!"

# Код привязки выдаётся только по Google-сессии (защита от увода чужой
# подписки), поэтому для теста подписываем cookie тем же APP_SECRET.
os.environ.setdefault("APP_SECRET", "tg-test-app-secret")
APP_SECRET = os.environ["APP_SECRET"]

def make_session(email, name="TG Test"):
    payload = f"{email}|{name}|{int(time.time())}"
    sig = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode().rstrip("=")

SESSION = make_session("tgtest@example.com")

out = open("tg_out.txt", "w", encoding="utf-8")
def log(*a):
    print(*a, file=out, flush=True)

BASE = "http://127.0.0.1:8791"
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", "8791"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ))

def post(path, timeout=10):
    req = urllib.request.Request(BASE + path, data=b"{}",
                                 headers={"Content-Type": "application/json",
                                          "Cookie": "rd_session=" + SESSION}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())

def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())

try:
    for _ in range(30):
        try:
            get("/", timeout=2); break
        except Exception:
            time.sleep(0.4)

    # 1) Генерация кода привязки
    status, j = post("/api/tg/link/start")
    log("LINK START:", status, json.dumps(j, ensure_ascii=False))
    assert status == 200 and "code" in j, "не удалось сгенерировать код"
    assert j.get("bot_username") == "RustDeckcryptobot", "неверный юзернейм бота"
    assert j.get("deep_link", "").startswith("https://t.me/RustDeckcryptobot?start="), "битый deep-link"
    log("CODE OK:", j["code"])

    # 2) Статус до привязки — pending
    status, j2 = get(f"/api/tg/link/status/{j['code']}")
    log("LINK STATUS:", status, j2)
    assert j2["status"] == "pending", "код должен быть pending"

    # 3) Неверный формат кода
    status, j3 = get("/api/tg/link/status/999999")
    log("FAKE CODE:", status, j3)
    assert j3["status"] in ("not_found", "expired"), "фейковый код должен не находиться"

    log("ALL TG API TESTS PASSED")
    log("ВАЖНО: бот-поток уже слушает Telegram. Отправь @RustDeckcryptobot /start —")
    log("если бот ответил, привязка работает. Далее: /link " + j["code"])
except Exception as e:
    log("TEST ERROR:", repr(e))
finally:
    proc.terminate()
    out.close()
