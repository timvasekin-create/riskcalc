# -*- coding: utf-8 -*-
# Быстрый smoke-тест: сервер + все роуты, результат в smoke_out.txt
import subprocess, sys, time, json, urllib.request, os

if os.path.exists("smoke_out.txt"):
    os.remove("smoke_out.txt")

BASE = "http://127.0.0.1:8779"
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", "8779"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

out = open("smoke_out.txt", "w", encoding="utf-8")
def log(*a):
    print(*a, file=out, flush=True)

def get(path, timeout=8):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

try:
    for _ in range(30):
        try:
            get("/", timeout=2); break
        except Exception:
            time.sleep(0.4)

    fails = 0
    log("=== HTTP checks ===")
    for path in ["/", "/bitcoin-risk-calculator", "/bybit-calculator", "/hyperliquid-calculator",
                 "/ethereum-risk-calculator", "/solana-risk-calculator",
                 "/leverage-calculator", "/liquidation-calculator",
                 "/robots.txt", "/sitemap.xml"]:
        try:
            status, body = get(path)
            ok = status == 200
            if not ok: fails += 1
            log(f"{'OK ' if ok else 'FAIL'} {status} {path} ({len(body)} bytes)")
        except Exception as e:
            fails += 1
            log(f"FAIL ERR {path}: {e}")

    payload = json.dumps({
        "margin": 1000, "risk_type": "amount", "risk_value": 50,
        "entry": 95000, "stop_loss": 94000, "take_profit": 97000,
        "leverage": 10, "direction": "long",
    }).encode()
    req = urllib.request.Request(BASE + "/api/calculate", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        calc = json.loads(r.read().decode())
    log("CALC:", json.dumps(calc))
    assert abs(calc["position_size"] - 10000) < 0.01
    assert abs(calc["risk_amount"] - 50) < 0.01
    log("CALC OK — математика не изменилась")

    try:
        status, body = get("/api/prices", timeout=15)
        prices = json.loads(body)
        log("PRICES:", json.dumps(prices, ensure_ascii=False)[:600])
        log(f"PRICES: {len(prices.get('prices', []))} монет" if prices.get("prices") else "PRICES WARN: пусто (нет сети/регион)")
    except Exception as e:
        log("PRICES WARN:", repr(e))

    # Telegram link API: локально без BOT_TOKEN ждём 503 (bot_disabled)
    try:
        status, body = get("/api/tg/link/status/000000", timeout=8)
        log(f"TG status endpoint: {status} {body[:100]}")
    except urllib.error.HTTPError as e:
        log(f"TG status endpoint: {e.code} (ожидаемо 503 без токена)" if e.code == 503 else f"TG status endpoint: {e.code} — ПРОВЕРИТЬ")
    except Exception as e:
        log("TG status WARN:", repr(e))
    try:
        req = urllib.request.Request(BASE + "/api/tg/link/start", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            log(f"TG start endpoint: {r.status} (бот включён?)")
    except urllib.error.HTTPError as e:
        log(f"TG start endpoint: {e.code} (ожидаемо 503 без токена)" if e.code == 503 else f"TG start endpoint: {e.code} — ПРОВЕРИТЬ")
    except Exception as e:
        log("TG start WARN:", repr(e))

    _, sm = get("/sitemap.xml")
    for p in ["/ethereum-risk-calculator", "/liquidation-calculator", "/leverage-calculator", "/solana-risk-calculator"]:
        assert p in sm, f"sitemap не содержит {p}"
    log("SITEMAP OK: все новые страницы на месте")

    _, html = get("/")
    for must in ["tickerItems", "Rust<span", "api/prices", "applyLivePrices", "entryTouchedByUser", "asset-price", "tgLinkBtn", "Connect Telegram"]:
        assert must in html, f"HTML не содержит {must}"
    for gone in ['data-mode="cross"', 'id="saveBtn"', 'availDisplay', ">Limit<", "Cross<"]:
        assert gone not in html, f"HTML всё ещё содержит {gone}"
    log("HTML OK: тикер на месте, мусор удалён")

    # Host-роутинг: Host rustdeck.app → хаб, Host calc.rustdeck.app → калькулятор
    req = urllib.request.Request(BASE + "/", headers={"Host": "rustdeck.app"})
    with urllib.request.urlopen(req, timeout=8) as r:
        hub = r.read().decode("utf-8", "replace")
    for must in ["Wallet Tracker", "RustDeckcryptobot", "Track Wallet", "marketsTable", "fundingHigh", "</html>"]:
        assert must in hub, f"Хаб не содержит {must}"
    assert "Calculate Position" not in hub, "хаб не должен быть калькулятором"
    log("HUB OK: rustdeck.app отдаёт wallet-tracker хаб")

    req2 = urllib.request.Request(BASE + "/", headers={"Host": "calc.rustdeck.app"})
    with urllib.request.urlopen(req2, timeout=8) as r:
        calc_page = r.read().decode("utf-8", "replace")
    assert "Calculate Position" in calc_page, "calc. должен отдавать калькулятор"
    assert "Wallet Tracker" not in calc_page, "калькулятор не должен быть хабом"
    # Футер калькулятора: только валидные ссылки
    for gone in ["bitcoin-risk-calculator", "bybit-calculator", "/leverage-calculator"]:
        assert gone not in calc_page.split("footer")[1], "в футере калькулятора остались невалидные ссылки"
    log("CALC HOST OK: калькулятор отдаётся, футер почищен")

    # Wallet API: реальный адрес (валидный формат; может быть 0 сделок — это ок)
    try:
        status, body = get("/api/wallet/0x000000000000000000000000000000000000dEaD", timeout=20)
        w = json.loads(body)
        log(f"WALLET API: {status}, account_value={w.get('account_value')}, positions={len(w.get('positions', []))}")
        assert "stats" in w, "нет stats в ответе"
    except urllib.error.HTTPError as e:
        log(f"WALLET API: {e.code} (HL может быть недоступен локально)")
    except Exception as e:
        log("WALLET API WARN:", repr(e))

    # Funding API
    try:
        status, body = get("/api/funding", timeout=20)
        f = json.loads(body)
        n = len(f.get("funding", []))
        log(f"FUNDING API: {status}, {n} монет")
    except Exception as e:
        log("FUNDING API WARN:", repr(e))

    log("FAILS:", fails)
    log("ALL SMOKE TESTS PASSED" if fails == 0 else "SOME CHECKS FAILED")
except Exception as e:
    log("TEST ERROR:", repr(e))
finally:
    proc.terminate()
    out.close()

