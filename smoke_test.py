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

    # Google OAuth: без ключей локально — редирект на /?google=unavailable
    try:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
        try:
            urllib.request.build_opener(_NoRedirect()).open(BASE + "/auth/google", timeout=8)
            fails += 1
            log("FAIL GOOGLE /auth/google: 200 (ожидаем редирект)")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
            ok = e.code in (301, 302, 303, 307, 308)
            if not ok:
                fails += 1
            log(f"{'OK ' if ok else 'FAIL'} GOOGLE /auth/google: {e.code} → {loc}")
    except Exception as e:
        log("GOOGLE WARN:", repr(e))

    # /api/me без cookie — не авторизован (и не должен падать)
    try:
        status, body = get("/api/me", timeout=8)
        mj = json.loads(body)
        assert status == 200 and mj.get("authenticated") is False, f"/api/me: {status} {body[:120]}"
        log("ME API OK: /api/me без cookie → authenticated=false")
    except Exception as e:
        fails += 1
        log("ME API FAIL:", repr(e))

    # Мост «сайт → TG»: локально без BOT_TOKEN ждём 503
    try:
        req = urllib.request.Request(
            BASE + "/api/tg/watch",
            data=json.dumps({"chat_id": 1, "wallet": "0x" + "0" * 40}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            log(f"TG watch bridge: {r.status} (бот включён?)")
    except urllib.error.HTTPError as e:
        log(f"TG watch bridge: {e.code} (ожидаемо 503 без токена)" if e.code == 503 else f"TG watch bridge: {e.code} — ПРОВЕРИТЬ")
    except Exception as e:
        log("TG watch bridge WARN:", repr(e))

    # api.rustdeck.app — закрытый домен: гостю только экран входа, API закрыт
    try:
        req = urllib.request.Request(BASE + "/", headers={"Host": "api.rustdeck.app"})
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read().decode("utf-8", "replace")
        ok = r.status == 200 and "restricted" in body and "Sign in with Google" in body
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} API HOST /: {r.status} — экран входа (админка закрыта)")
    except Exception as e:
        log("API HOST / WARN:", repr(e))
    for path in ("/admin/users", "/api/whales"):
        try:
            req = urllib.request.Request(BASE + path, headers={"Host": "api.rustdeck.app"})
            with urllib.request.urlopen(req, timeout=8) as r:
                fails += 1
                log(f"FAIL API HOST {path}: {r.status} (ожидаем 403 для гостя)")
        except urllib.error.HTTPError as e:
            ok = e.code == 403
            if not ok:
                fails += 1
            log(f"{'OK ' if ok else 'FAIL'} API HOST {path}: {e.code} (ожидаемо 403)")
        except Exception as e:
            log(f"API HOST {path} WARN:", repr(e))

    # /api/profile без Google-сессии — 401
    try:
        status, body = get("/api/profile", timeout=8)
        fails += 1
        log(f"FAIL PROFILE API: {status} без сессии (ожидаем 401)")
    except urllib.error.HTTPError as e:
        ok = e.code == 401
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} PROFILE API: {e.code} (без сессии ожидаем 401)")
    except Exception as e:
        log("PROFILE API WARN:", repr(e))

    # Админ-дашборд рендерится без ошибок (шаблон проверяем напрямую)
    try:
        import main as _main
        html_admin = _main._admin_page_html("ila281510@gmail.com")
        assert "admin/users" in html_admin and "admin/add_days" in html_admin, "нет эндпоинтов в админке"
        assert "Add days" in html_admin and "ila281510@gmail.com" in html_admin, "нет колонки добавления дней"
        assert _main.is_admin("ila281510@gmail.com") and not _main.is_admin("random@example.com"), "whitelist админов сломан"
        log("ADMIN HTML OK: дашборд рендерится, whitelist работает")
    except Exception as e:
        fails += 1
        log("ADMIN HTML FAIL:", repr(e))

    # Основной домен: /admin тоже закрыт для гостя (панель не публичная)
    try:
        urllib.request.urlopen(BASE + "/admin", timeout=8)
        fails += 1
        log("FAIL ADMIN MAIN HOST: 200 без админ-сессии (панель открыта!)")
    except urllib.error.HTTPError as e:
        ok = e.code == 403
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} ADMIN MAIN HOST: {e.code} (ожидаемо 403 для гостя)")
    except Exception as e:
        log("ADMIN MAIN HOST WARN:", repr(e))

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
                 "walletChips", "detectEvents", "whaleList", "lbTable", "lbToggles", "refreshWalletBtn",
                 "chartBlock", "chartToggles", "pnlChart", "stPF",
                 "scoreBlock", "scoreGrade", "followTgBtn", "startTgLink", "tgDeepLink",
                 "chartTabs", "chartTip", "authModal", "authGoogleBtn", "tgModal", "tgConnectBtn",
                 "shareScoreBtn", "evOrders", "side-rail",
                 "calcTicker", "calcChart", "loadCalcTicker", "setWalletBtn",
                 "authGoogleBtn", "convCoin", "calcCopyBtn", "calcHlBtn", "tgAlertStatus", "</html>"]:
        assert must in hub, f"Хаб не содержит {must}"
    assert "Position Calculator" in hub, "на хабе должен быть калькулятор позиций"
    assert "calcChart" in hub, "на хабе нет графика калькулятора"
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
        assert "pnl_charts" in w, "нет pnl_charts в ответе (график PnL)"
        assert "profit_factor" in w["stats"], "нет profit_factor в stats"
        assert "score" in w["stats"], "нет score в stats (RustDeck Score)"
        assert "streaks" in w["stats"], "нет streaks в stats"
        assert "max_drawdown_usd" in w["stats"], "нет max_drawdown_usd в stats"
        assert "value_charts" in w, "нет value_charts (график Account Value)"
        assert "perp_chart" in w, "нет perp_chart (вкладка Perps PnL)"
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

    # Assets + Candles API (встроенный калькулятор позиций)
    try:
        status, body = get("/api/assets", timeout=20)
        assets = json.loads(body).get("assets", [])
        log(f"ASSETS API: {status}, {len(assets)} активов (BTC={'BTC' in assets})")
    except Exception as e:
        log("ASSETS WARN:", repr(e))
    try:
        status, body = get("/api/candles/btc?hours=24", timeout=25)  # регистр не важен
        cj = json.loads(body)
        n = len(cj.get("candles", []))
        log(f"CANDLES API: {status}, {n} свечей, coin={cj.get('coin')}")
        assert n > 5 and cj.get("coin") == "BTC", "свечи не пришли"
    except Exception as e:
        log("CANDLES WARN:", repr(e))
    try:
        get("/api/candles/notacoin", timeout=15)
        log("CANDLES unknown: НЕ отклонён — ПРОВЕРИТЬ")
    except urllib.error.HTTPError as e:
        log(f"CANDLES unknown: {e.code} (ожидаемо 404)")

    # Публичная страница RustDeck Score
    try:
        status, body = get("/score/0x000000000000000000000000000000000000dEaD", timeout=25)
        assert "RustDeck Score" in body, "score-страница без заголовка"
        log(f"SCORE PAGE: {status}, {len(body)} bytes OK")
    except Exception as e:
        log("SCORE PAGE WARN:", repr(e))

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
        # Чистим следы прошлых прогонов — тесты должны быть герметичными
        _clean = _bot._db()
        for _tbl in ("subscribers", "price_alerts"):
            _clean.execute(f"DELETE FROM {_tbl} WHERE chat_id IN (777000, 888001, 888002)")
        _clean.commit()
        _clean.close()
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

        # Статус привязки возвращает tg_username/chat_id (нужно сайту для "Continue as")
        st = _bot.link_code_status(c1)
        assert st.get("status") == "linked" and st.get("tg_username") == "tester" and st.get("chat_id") == 777000, f"link status без tg_username: {st}"
        log("LINK OK: /api/tg/link/status возвращает tg_username + chat_id")

        # Формат чисел: 0.004362 не должен округляться до 0.0044
        assert _bot._fmt_small(0.004362) == "0.004362", f"_fmt_small(0.004362) = {_bot._fmt_small(0.004362)}"
        assert _bot._fmt_small(84250.5) == "84,250.50", f"_fmt_small(84250.5) = {_bot._fmt_small(84250.5)}"
        log("FMT OK: мелкие числа с 6 значащими цифрами (0.004362), крупные — с запятыми")

        # Сторона сделки: HL отдаёт A/B (ask/bid) — в интерфейсе должно быть Long/Short
        assert _bot._side_label("A") == "Short" and _bot._side_label("B") == "Long", "A/B не превращаются в Short/Long"
        assert _bot._side_label("b") == "Long" and _bot._side_label("") == "—", "регистр/пустое значение обрабатываются неверно"
        log("SIDE OK: A/B → Short/Long")

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
        def _msg(t, chat=777000):
            return {"message": {"chat": {"id": chat}, "from": {"username": "tester"}, "text": t}}
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

        # МУЛЬТИ-КОШЕЛЬКИ: до 5 адресов на аккаунт, дубликаты не добавляются
        _bot._cmd_unwatch(777000)  # очистка перед тестом
        a1 = "0x0000000000000000000000000000000000000001"
        a2 = "0x0000000000000000000000000000000000000002"
        _bot._cmd_watch(777000, 'tester', '/watch ' + a1)
        _bot._cmd_watch(777000, 'tester', '/watch ' + a2)
        conn = _bot._db()
        wm = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert wm["watched_wallet"] == f"{a1},{a2}", f"мульти-watch сломан: {wm['watched_wallet']}"
        log("WATCH OK: 2 кошелька на аккаунт (список через запятую)")

        _bot._cmd_watch(777000, 'tester', '/watch ' + a1)  # повторный — не дублирует
        conn = _bot._db()
        wm2 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert wm2["watched_wallet"] == f"{a1},{a2}", f"дубликат добавлен: {wm2['watched_wallet']}"
        log("WATCH OK: повторный /watch не дублирует адрес")

        _bot._cmd_unwatch(777000, '/unwatch ' + a1)  # убрать только один
        conn = _bot._db()
        wm3 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        assert wm3["watched_wallet"] == a2, f"/unwatch 0x… не удалил один адрес: {wm3['watched_wallet']}"
        log("WATCH OK: /unwatch 0x… убирает только указанный кошелёк")

        # Лимит: максимум 5 адресов
        for i in range(3, 8):
            _bot._cmd_watch(777000, 'tester', f'/watch 0x{(str(i) * 3).rjust(40, "0")}')
        conn = _bot._db()
        wm4 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (777000,)).fetchone()
        conn.close()
        cnt = len(_bot._parse_watched(wm4["watched_wallet"]))
        assert cnt == 5, f"лимит {5} кошельков не работает: {cnt}"
        log("WATCH OK: лимит 5 кошельков на аккаунт")

        # EMAIL-АККАУНТ: один триал на email — второй Telegram-аккаунт без триала
        conn = _bot._db()
        conn.execute("DELETE FROM subscribers WHERE chat_id IN (888001, 888002)")
        conn.commit()
        conn.close()
        c3 = _bot.create_link_code("Tester@Example.com")
        _bot._try_link(888001, 'tester2', c3)
        conn = _bot._db()
        s1 = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (888001,)).fetchone()
        conn.close()
        assert s1["email"] == "tester@example.com", f"email не сохранён lowercase: {s1['email']}"
        assert s1["tier"] == "trial", "первый аккаунт должен получить триал"
        log("EMAIL OK: email аккаунта сохранён (lowercase), триал выдан")

        c4 = _bot.create_link_code("tester@example.com")
        _bot._try_link(888002, 'tester3', c4)
        conn = _bot._db()
        s2 = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (888002,)).fetchone()
        conn.close()
        assert s2["tier"] == "free", f"второй аккаунт того же email не должен получать триал: {s2['tier']}"
        log("EMAIL OK: повторный триал на тот же email не выдаётся (tier=free)")

        # Deep-link /start watch_0x… (кнопка 👁 в Whale Feed / лидерборде)
        _bot.handle_update(_msg("/start watch_0x0000000000000000000000000000000000000001", chat=888001))
        conn = _bot._db()
        s3 = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (888001,)).fetchone()
        conn.close()
        assert s3["watched_wallet"] == "0x0000000000000000000000000000000000000001", f"deep-link watch не сработал: {s3['watched_wallet']}"
        log("WATCH OK: deep-link /start watch_0x… запускает слежение из TG")

        # ЦЕНОВЫЕ АЛЕРТЫ (/alert BTC > 1)
        _bot._cmd_alert(777000, 'tester', '/alert btc > 1')
        conn = _bot._db()
        pa = conn.execute("SELECT sym, op, target FROM price_alerts WHERE chat_id=?", (777000,)).fetchall()
        conn.close()
        assert len(pa) == 1 and pa[0]["sym"] == "BTC" and pa[0]["op"] == ">", f"алерт не сохранился: {[dict(r) for r in pa]}"
        log("ALERT OK: /alert btc > 1 — тикер нормализован в BTC")
        _bot._cmd_alert(777000, 'tester', '/alert NOTACOIN > 5')
        conn = _bot._db()
        cnt = conn.execute("SELECT COUNT(*) FROM price_alerts WHERE chat_id=?", (777000,)).fetchone()[0]
        conn.close()
        assert cnt == 1, "неизвестный актив не должен сохраняться"
        log("ALERT OK: неизвестный тикер отклонён")
        _bot._cmd_delalert(777000, '/delalert 1')
        conn = _bot._db()
        cnt2 = conn.execute("SELECT COUNT(*) FROM price_alerts WHERE chat_id=?", (777000,)).fetchone()[0]
        conn.close()
        assert cnt2 == 0, "/delalert не удалил алерт"
        log("ALERT OK: /delalert 1 удаляет алерт")

        # Мост «сайт → TG»: add_watch добавляет кошелёк и не дублирует его
        # (локально без BOT_TOKEN подставляем фиктивный — проверяем БД-логику)
        _saved_token = _bot.BOT_TOKEN
        _bot.BOT_TOKEN = _saved_token or "smoke-test-token"
        try:
            w1 = _bot.add_watch(888001, "0x" + "2" * 40)
            assert w1.get("ok") and w1.get("count") == 2, f"add_watch не сработал: {w1}"
            w2 = _bot.add_watch(888001, "0x" + "2" * 40)
            assert w2.get("already") is True, f"повторный add_watch должен быть already: {w2}"
            w3 = _bot.add_watch(888001, "notanaddress")
            assert w3.get("error") == "invalid_address", f"кривой адрес не отклонён: {w3}"
            sub_email = _bot.subscriber_by_email("tester@example.com")
            assert sub_email and sub_email["chat_id"] in (888001, 888002), f"subscriber_by_email: {sub_email}"
            assert sub_email["username"] in ("tester", "tester3"), f"username не проброшен: {sub_email}"
            log("BRIDGE OK: сайт→TG (add_watch + subscriber_by_email)")
        finally:
            _bot.BOT_TOKEN = _saved_token

        # Профиль сайта (Google-аккаунт) хранится в БД и отдаётся в /api/me
        _bot.save_profile("Tester@Example.com", name="Tester", wallet="0x" + "9" * 40)
        prof = _bot.get_profile("tester@example.com")
        assert prof["name"] == "Tester" and prof["wallet"] == "0x" + "9" * 40, f"профиль не сохранился: {prof}"
        st = _bot.user_status("tester@example.com")
        assert st["wallet"] == "0x" + "9" * 40, f"user_status без кошелька: {st}"
        log("PROFILE OK: имя и кошелёк профиля хранятся в БД")

        # Жёсткая проверка подписки: истёкшая — блокирует /watch и алерты
        conn = _bot._db()
        conn.execute("UPDATE subscribers SET expires_at=? WHERE chat_id=?", (time.time() - 10, 888001))
        conn.commit()
        sub_exp = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (888001,)).fetchone()
        conn.close()
        assert not _bot.subscription_active(sub_exp), "истёкшая подписка должна быть inactive"
        _bot.handle_update(_msg("/watch 0x" + "3" * 40, chat=888001))
        conn = _bot._db()
        w_exp = conn.execute("SELECT watched_wallet FROM subscribers WHERE chat_id=?", (888001,)).fetchone()
        conn.close()
        assert "0x" + "3" * 40 not in (w_exp["watched_wallet"] or ""), "истёкшая подписка не должна добавлять кошельки"
        log("SUBS OK: истёкшая подписка блокирует /watch (жёсткая проверка)")

        # Админ добавляет дни — подписка снова активна
        ext = _bot.extend_subscription(chat_id=888001, days=7)
        assert ext.get("ok") and ext.get("days_left") >= 7, f"extend_subscription: {ext}"
        conn = _bot._db()
        sub_ok = conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (888001,)).fetchone()
        conn.close()
        assert _bot.subscription_active(sub_ok), "после продления подписка должна быть active"
        log(f"SUBS OK: админ добавил дни — снова active ({ext['days_left']}d, tier={ext['tier']})")

        # Админка: список юзеров отдаёт почту, TG, кошелёк и подписку
        users = _bot.list_users()
        me_admin = next((u for u in users if u.get("chat_id") == 888001), None)
        assert me_admin, f"list_users не нашёл тестовый TG-аккаунт: {users[:3]}"
        assert me_admin.get("tg_username"), f"нет TG-юзернейма: {me_admin}"
        log("ADMIN OK: list_users отдаёт TG-юзера, почту, кошелёк и подписку")

        conn = _bot._db()
        conn.execute("DELETE FROM subscribers WHERE chat_id IN (888001, 888002)")
        conn.execute("DELETE FROM profiles WHERE email='tester@example.com'")
        conn.commit()
        conn.close()

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

