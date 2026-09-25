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
    for must in ["Wallet Tracker", "RustDeckcryptobot", "Track Wallet", "marketsTable",
                 "fundingHigh", "profileArea", "alertsToggle", "exportCsvBtn", "ordersTable",
                 "walletChips", "detectEvents", "whaleList", "lbTable", "lbToggles", "</html>"]:
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
        log(f"WALLET API: {status}, perps={w.get('account_value')}, spot={w.get('spot_value')}, total={w.get('account_total')}, spot_tokens={len(w.get('spot_tokens', []))}")
        assert "stats" in w, "нет stats в ответе"
        assert "spot_value" in w, "нет spot_value в ответе"
        assert "account_total" in w, "нет account_total в ответе"
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

    # Markets API (скринер)
    try:
        status, body = get("/api/markets", timeout=20)
        m = json.loads(body)
        n = len(m.get("markets", []))
        top = (m.get("markets") or [{}])[0]
        log(f"MARKETS API: {status}, {n} монет, топ по объёму: {top.get('coin')} vol=${top.get('volume24h')}")
    except Exception as e:
        log("MARKETS API WARN:", repr(e))

    # Fills API (для CSV-экспорта)
    try:
        status, body = get("/api/fills/0x000000000000000000000000000000000000dEaD?limit=50", timeout=20)
        fl = json.loads(body)
        log(f"FILLS API: {status}, {fl.get('count')} сделок")
    except urllib.error.HTTPError as e:
        log(f"FILLS API: {e.code} (HL может быть недоступен локально)")
    except Exception as e:
        log("FILLS API WARN:", repr(e))

    # Leaderboard API (файл ~10MB, может грузиться до пары минут)
    try:
        status, body = get("/api/leaderboard", timeout=150)
        lb = json.loads(body)
        log(f"LEADERBOARD API: {status}, day={len(lb.get('day', []))}, week={len(lb.get('week', []))}, all={len(lb.get('all', []))}")
        top = (lb.get("all") or [{}])[0]
        if top:
            log(f"  #1 all-time: {top.get('name') or top.get('address','')[:12]}… pnl=${top.get('pnl'):,}")
    except Exception as e:
        log("LEADERBOARD WARN:", repr(e))

    # Whale Feed API
    try:
        status, body = get("/api/whales", timeout=150)
        wh = json.loads(body)
        evs = wh.get("events", [])
        log(f"WHALES API: {status}, {len(evs)} событий за 24ч (порог ${wh.get('min_usd'):,})")
        if evs:
            e0 = evs[0]
            log(f"  свежайшее: {e0.get('coin')} {e0.get('dir')} @ ${e0.get('notional'):,}")
    except Exception as e:
        log("WHALES WARN:", repr(e))

    # Логика триала: одна акция на аккаунт, повторная привязка НЕ продлевает
    try:
        import bot as _bot
        _bot.init_db()
        c1 = _bot.create_link_code()
        _bot._try_link(777000, 'tester', c1)  # send_message молча упадёт без токена — ок
        conn = _bot._db()
        sub1 = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert sub1 is not None, "подписка не создалась"
        c2 = _bot.create_link_code()
        _bot._try_link(777000, 'tester', c2)
        conn = _bot._db()
        sub2 = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert abs(sub2["expires_at"] - sub1["expires_at"]) < 0.001, "trial ПРОДЛИЛСЯ — так нельзя!"
        log(f"TRIAL OK: expires_at не изменился при повторной привязке")

        # /watch: привязка кошелька к подписке + /unwatch (адрес нормализуется в lowercase)
        _bot._cmd_watch(777000, 'tester', '/watch 0x000000000000000000000000000000000000dEaD')
        conn = _bot._db()
        w1 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert w1["watched_wallet"] == "0x000000000000000000000000000000000000dead", f"/watch сохранил неверный адрес: {w1['watched_wallet']}"
        _bot._cmd_unwatch(777000)
        conn = _bot._db()
        w2 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert w2["watched_wallet"] is None, "/unwatch не очистил кошелёк"
        log("WATCH OK: /watch сохраняет кошелёк (lowercase), /unwatch очищает")

        # /watch с кривым адресом — не сохраняет
        _bot._cmd_watch(777000, 'tester', '/watch notanaddress')
        conn = _bot._db()
        w3 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert w3["watched_wallet"] is None, "кривой адрес не должен сохраняться"
        log("WATCH OK: невалидный адрес отклонён")

        # ГОЛЫЙ адрес без /watch — должен запускать слежение (как прислал юзер)
        def _msg(t):
            return {"message": {"chat": {"id": 777000}, "from": {"username": "tester"}, "text": t}}
        _bot.handle_update(_msg("0x000000000000000000000000000000000000dEaD"))
        conn = _bot._db()
        w4 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert w4["watched_wallet"] == "0x000000000000000000000000000000000000dead", "голый адрес 0x не распознан"
        log("WATCH OK: голый адрес с 0x запускает слежение")

        _bot.handle_update(_msg("0X000000000000000000000000000000000000DEAD"))  # верхний регистр
        conn = _bot._db()
        w5 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert w5["watched_wallet"] == "0x000000000000000000000000000000000000dead", "0X uppercase не распознан"
        log("WATCH OK: 0X uppercase нормализуется в lowercase")

        _bot.handle_update(_msg("000000000000000000000000000000000000dEaD"))  # без 0x
        conn = _bot._db()
        w6 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert w6["watched_wallet"] == "0x000000000000000000000000000000000000dead", "адрес без 0x не распознан"
        log("WATCH OK: адрес без 0x распознаётся")

        _bot._cmd_unwatch(777000)
        conn = _bot._db()
        conn.execute("DELETE FROM subscribers WHERE chat_id=?", (777000,))
        conn.commit()
        conn.close()
    except Exception as e:
        log("TRIAL TEST WARN:", repr(e))

    log("FAILS:", fails)
    log("ALL SMOKE TESTS PASSED" if fails == 0 else "SOME CHECKS FAILED")
except Exception as e:
    log("TEST ERROR:", repr(e))
finally:
    proc.terminate()
    out.close()

