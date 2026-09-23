# -*- coding: utf-8 -*-
# Тест бота с реальным токеном: BOT_TOKEN берём из окружения (передаётся при запуске)
import subprocess, sys, time, json, urllib.request, os

assert os.environ.get("BOT_TOKEN"), "BOT_TOKEN не задан в окружении!"

out = open("tg_out.txt", "w", encoding="utf-8")
def log(*a):
    print(*a, file=out, flush=True)

BASE = "http://127.0.0.1:8791"
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", "8791"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def post(path, timeout=10):
    req = urllib.request.Request(BASE + path, data=b"{}",
                                 headers={"Content-Type": "application/json"}, method="POST")
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
