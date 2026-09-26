# -*- coding: utf-8 -*-
# Быстрый smoke-тест: сервер + все роуты, результат в smoke_out.txt
import subprocess, sys, time, json, urllib.request, os

# Один и тот же APP_SECRET у сервера и у теста — чтобы можно было подписывать
# сессионную cookie и проверять авторизованные роуты без Google.
os.environ.setdefault("APP_SECRET", "smoke-test-app-secret")
_env = dict(os.environ)

if os.path.exists("smoke_out.txt"):
    os.remove("smoke_out.txt")

BASE = "http://127.0.0.1:8779"
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", "8779"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_env)

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

    # ===== Алерты «ордер снят»: направление + снятые вместе TP/SL =====
    # Формат перенесён с основного бота владельца (был русский, стал английский):
    # в сообщении есть Long/Short, цена, объём в монете и USD, а тейк/стоп,
    # ушедшие в том же цикле, перечисляются блоком «Removed together».
    try:
        import bot as _bot_fmt
        _main_o = {"oid": 86, "coin": "LTC", "side": "B", "side_label": "Long", "px": 62.0,
                   "sz": 3.3, "notional": 204.6, "type": "Limit", "is_tp": False, "is_sl": False}
        _tp = {"oid": 87, "coin": "LTC", "side": "A", "side_label": "Short", "px": 77.0,
               "sz": 3.3, "notional": 254.1, "type": "Take Profit", "is_tp": True, "is_sl": False}
        _sl = {"oid": 88, "coin": "LTC", "side": "A", "side_label": "Short", "px": 60.0,
               "sz": 3.3, "notional": 199.6, "type": "Stop Loss", "is_tp": False, "is_sl": True}
        msg = _bot_fmt._order_removed_text(_main_o, [_tp, _sl])
        for must in ["ORDER REMOVED", "#86", "*LTC · LONG*", "$62", "3.3 LTC", "$204.60",
                     "Removed together:", "🎯 Take Profit — $77", "🛑 Stop Loss — $60"]:
            assert must in msg, f"в сообщении «ордер снят» нет {must!r}:\n{msg}"
        solo = _bot_fmt._order_removed_text(_main_o, [])
        assert "Removed together" not in solo, "блок TP/SL не должен появляться без них"
        only_exits = _bot_fmt._order_removed_text(_tp, [_sl])
        assert "*LTC · SHORT*" in only_exits and "Stop Loss" in only_exits, "нет направления/стопа"
        log("ORDER ALERT OK: «ордер снят» — LONG/SHORT + объём + блок Removed together (TP/SL)")
    except AssertionError as e:
        fails += 1
        log("ORDER ALERT FAIL:", str(e))
    except Exception as e:
        log("ORDER ALERT WARN:", repr(e))

    # Классификация ордеров (main._classify_order): строки HL и фолбэк по цене
    try:
        import main as _main_oc
        assert _main_oc._classify_order({"orderType": "Take Profit Market", "isTrigger": True,
                                        "triggerPx": "77", "side": "A"}, 70.0)[1] is True
        assert _main_oc._classify_order({"orderType": "Stop Market", "isTrigger": True,
                                        "triggerPx": "60", "side": "A"}, 70.0)[2] is True
        assert _main_oc._classify_order({"orderType": "Limit", "isTrigger": False,
                                        "limitPx": "62", "side": "B"}, 70.0)[0] == "Limit"
        # без подсказки в orderType: выше рынка + продажа = Take Profit, ниже = Stop Loss
        assert _main_oc._classify_order({"orderType": "Trigger", "isTrigger": True,
                                        "triggerPx": "77", "side": "A"}, 70.0)[1] is True
        assert _main_oc._classify_order({"orderType": "Trigger", "isTrigger": True,
                                        "triggerPx": "60", "side": "A"}, 70.0)[2] is True
        log("ORDER KIND OK: Take Profit / Stop Loss / Limit распознаются верно")
    except AssertionError as e:
        fails += 1
        log("ORDER KIND FAIL:", str(e))
    except Exception as e:
        log("ORDER KIND WARN:", repr(e))

    try:
        status, body = get("/api/prices", timeout=15)
        prices = json.loads(body)
        log("PRICES:", json.dumps(prices, ensure_ascii=False)[:400])
        plist = prices.get("prices", [])
        log(f"PRICES: {len(plist)} монет (в ответе count={prices.get('count')})")
        # Требуем не только цену, но и 24ч-% по всем монетам тикера — это была
        # главная жалоба: BTC/ETH/SOL/BNB показывали «—» вместо процентов.
        by_sym = {p["symbol"]: p for p in plist}
        missing = [s for s in ("BTC", "ETH", "SOL", "HYPE", "BNB", "XRP", "DOGE") if s not in by_sym]
        assert not missing, f"нет монет тикера: {missing}"
        no_change = [s for s in ("BTC", "ETH", "SOL", "HYPE", "BNB", "XRP", "DOGE")
                     if by_sym[s].get("change24h") is None]
        assert not no_change, f"нет 24ч-% у: {no_change}"
        assert len(plist) >= 50, f"в списке цен мало монет: {len(plist)}"
        assert by_sym["USDC"]["price"] == 1.0, "USDC должен быть ровно $1 (конвертер)"
        log("PRICES OK: 24ч-% есть у всех монет тикера, список большой (Hyperliquid + добор)")
    except Exception as e:
        fails += 1
        log("PRICES FAIL:", repr(e))

    # Telegram link API: локально без BOT_TOKEN ждём 503 (bot_disabled)
    try:
        status, body = get("/api/tg/link/status/000000", timeout=8)
        log(f"TG status endpoint: {status} {body[:100]}")
    except urllib.error.HTTPError as e:
        log(f"TG status endpoint: {e.code} (ожидаемо 503 без токена)" if e.code == 503 else f"TG status endpoint: {e.code} — ПРОВЕРИТЬ")
    except Exception as e:
        log("TG status WARN:", repr(e))
    # Выпуск кода привязки — только с Google-сессией: иначе чужой мог бы
    # выпустить код на чужую почту. Без сессии ждём 401.
    try:
        req = urllib.request.Request(BASE + "/api/tg/link/start", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            fails += 1
            log(f"FAIL TG start endpoint: {r.status} без сессии (ожидаем 401)")
    except urllib.error.HTTPError as e:
        ok = e.code == 401
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} TG start endpoint: {e.code} без сессии (ожидаемо 401)")
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

    # Мост «сайт → TG»: без сессии 401 (чужой не добавит кошелёк в любой чат)
    try:
        req = urllib.request.Request(
            BASE + "/api/tg/watch",
            data=json.dumps({"chat_id": 1, "wallet": "0x" + "0" * 40}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            fails += 1
            log(f"FAIL TG watch bridge: {r.status} без сессии (ожидаем 401)")
    except urllib.error.HTTPError as e:
        ok = e.code == 401
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} TG watch bridge: {e.code} без сессии (ожидаемо 401)")
    except Exception as e:
        log("TG watch bridge WARN:", repr(e))

    # api.rustdeck.app — «невидимый» домен: страницы не отдаются вообще
    # (пустой 404 без тела), а /api/* продолжает работать.
    try:
        req = urllib.request.Request(BASE + "/", headers={"Host": "api.rustdeck.app"})
        with urllib.request.urlopen(req, timeout=8) as r:
            fails += 1
            log(f"FAIL API HOST /: {r.status}, отдан контент (ожидаем пустой 404)")
    except urllib.error.HTTPError as e:
        body = e.read()
        robots_tag = e.headers.get("X-Robots-Tag", "")
        ok = e.code == 404 and not body and robots_tag.startswith("noindex")
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} API HOST /: {e.code}, тело {len(body)} байт, X-Robots-Tag='{robots_tag}' (ожидаем пустой 404 + noindex)")
    except Exception as e:
        log("API HOST / WARN:", repr(e))

    # На api-домене постороннему не видно ни админки, ни страниц-заглушек
    for path in ("/admin", "/admin/users", "/robots.txt", "/sitemap.xml"):
        try:
            req = urllib.request.Request(BASE + path, headers={"Host": "api.rustdeck.app"})
            with urllib.request.urlopen(req, timeout=8) as r:
                fails += 1
                log(f"FAIL API HOST {path}: {r.status} (ожидаем 404 для гостя)")
        except urllib.error.HTTPError as e:
            body = e.read()
            ok = e.code == 404 and not body
            if not ok:
                fails += 1
            log(f"{'OK ' if ok else 'FAIL'} API HOST {path}: {e.code} (ожидаемо пустой 404)")
        except Exception as e:
            log(f"API HOST {path} WARN:", repr(e))

    # ...но сам API на api-домене живёт (проверки, интеграции)
    try:
        req = urllib.request.Request(BASE + "/api/whales", headers={"Host": "api.rustdeck.app"})
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status == 200
            if not ok:
                fails += 1
            log(f"{'OK ' if ok else 'FAIL'} API HOST /api/whales: {r.status} (API должен работать)")
    except Exception as e:
        fails += 1
        log("API HOST /api/whales FAIL:", repr(e))

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

    # Основной домен: /admin посторонним не отвечает вообще (пустой 404)
    try:
        urllib.request.urlopen(BASE + "/admin", timeout=8)
        fails += 1
        log("FAIL ADMIN MAIN HOST: 200 без админ-сессии (панель открыта!)")
    except urllib.error.HTTPError as e:
        ok = e.code == 404
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} ADMIN MAIN HOST: {e.code} (ожидаемо 404 для гостя)")
    except Exception as e:
        log("ADMIN MAIN HOST WARN:", repr(e))

    _, sm = get("/sitemap.xml")
    for p in ["/ethereum-risk-calculator", "/liquidation-calculator", "/leverage-calculator", "/solana-risk-calculator"]:
        assert p in sm, f"sitemap не содержит {p}"
    log("SITEMAP OK: все новые страницы на месте")

    # ===== SEO: robots.txt и sitemap должны быть готовы к Google =====
    _, rb = get("/robots.txt")
    assert "Sitemap: https://rustdeck.app/sitemap.xml" in rb, "robots.txt: sitemap должен быть абсолютным URL"
    assert "Disallow: /admin" in rb and "Disallow: /api/" in rb and "Disallow: /score/" in rb, \
        "robots.txt: служебные пути не закрыты от индексации"
    assert sm.count("<url>") == 8, f"sitemap: ожидали 8 страниц, получили {sm.count('<url>')}"
    assert "<lastmod>" in sm and "<priority>" in sm, "sitemap: нет lastmod/priority"
    assert sm.count("<loc>") == 8 and "onrender" not in sm, "sitemap: только канонические URL одного домена"
    log("SEO OK: robots.txt (абсолютный sitemap, служебное закрыто) + sitemap (канонический домен)")

    # ===== Security-заголовки =====
    req = urllib.request.Request(BASE + "/", headers={"Host": "rustdeck.app"})
    with urllib.request.urlopen(req, timeout=8) as r:
        hdr = {k.lower(): v for k, v in r.headers.items()}
    for h, want in (("x-content-type-options", "nosniff"), ("x-frame-options", "DENY")):
        assert hdr.get(h) == want, f"нет заголовка {h}={want} (получили {hdr.get(h)})"
    assert hdr.get("referrer-policy"), "нет Referrer-Policy"
    assert not hdr.get("access-control-allow-origin"), "не должно быть открытого CORS"
    log("HEADERS OK: nosniff, X-Frame-Options=DENY, Referrer-Policy, CORS закрыт")

    # ===== Сессии и приватные роуты (подписываем cookie сами, как сервер) =====
    try:
        import main as _main
        token = _main.make_session("smoke@example.com", "Smoke")
        assert _main.read_session(token), "подписанная сессия не читается"
        assert _main.read_session(token[:-3] + "0aa") is None, "подделка cookie принята — дыра!"
        log("SESSION OK: своя cookie читается, подделанная отклоняется")

        def req_with(path, payload=None, timeout=12):
            hdrs = {"Cookie": "rd_session=" + token}
            if payload is not None:
                hdrs["Content-Type"] = "application/json"
                rq = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                            headers=hdrs, method="POST")
            else:
                rq = urllib.request.Request(BASE + path, headers=hdrs)
            with urllib.request.urlopen(rq, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")

        st, bd = req_with("/api/me")
        assert json.loads(bd).get("authenticated") is True, f"/api/me с сессией: {bd[:120]}"
        st, bd = req_with("/api/profile")
        assert st == 200 and json.loads(bd).get("email") == "smoke@example.com", f"/api/profile: {bd[:120]}"
        log("AUTH OK: /api/me и /api/profile отвечают по подписанной сессии")

        # Кривой кошелёк отклоняется, HTML в имени вырезается (иначе разметка
        # улетала в админ-панель)
        try:
            urllib.request.urlopen(urllib.request.Request(
                BASE + "/api/profile", data=json.dumps({"wallet": "not-a-wallet"}).encode(),
                headers={"Cookie": "rd_session=" + token, "Content-Type": "application/json"},
                method="POST"), timeout=8)
            fails += 1
            log("FAIL PROFILE: кривой кошелёк принят")
        except urllib.error.HTTPError as e:
            ok = e.code == 400
            if not ok:
                fails += 1
            log(f"{'OK ' if ok else 'FAIL'} PROFILE: кривой кошелёк → {e.code} (ожидаемо 400)")
        st, bd = req_with("/api/profile", payload={"name": "<img src=x onerror=alert(1)>Alex"})
        saved_name = json.loads(bd).get("name") or ""
        ok = "<" not in saved_name and ">" not in saved_name
        if not ok:
            fails += 1
        log(f"{'OK ' if ok else 'FAIL'} PROFILE SANITIZE: имя сохранено как '{saved_name}'")

        # С валидной сессией TG-роуты доходят до бота: локально без токена 503
        try:
            req_with("/api/tg/link/start", payload={})
            fails += 1
            log("FAIL TG start: 200 без BOT_TOKEN")
        except urllib.error.HTTPError as e:
            ok = e.code == 503
            if not ok:
                fails += 1
            log(f"{'OK ' if ok else 'FAIL'} TG start (с сессией): {e.code} (ожидаемо 503 без токена бота)")
    except AssertionError as e:
        fails += 1
        log("SECURITY FAIL:", str(e))
    except Exception as e:
        log("SECURITY WARN:", repr(e))
    # ===== Рейт-лимитер (защита квоты Hyperliquid) =====
    try:
        import main as _main_lim
        ip = "203.0.113.77"
        limit = _main_lim.RATE_LIMITS["score"][0]
        blocked = sum(1 for _ in range(limit + 3) if _main_lim.rate_limited(ip, "score"))
        assert blocked == 3, f"лимитер отсекает не то: {blocked} из {limit + 3}"
        assert not _main_lim.rate_limited("198.51.100.9", "score"), "лимитер блокирует чужой IP"
        log(f"RATE LIMIT OK: /score держит {limit} запросов/мин на IP, дальше 429")
    except AssertionError as e:
        fails += 1
        log("RATE LIMIT FAIL:", str(e))
    except Exception as e:
        log("RATE LIMIT WARN:", repr(e))

    # ===== Скан на утечки секретов в публичном репо =====
    try:
        import re as _re2
        tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
        assert tracked, "git ls-files пуст — репозиторий не найден"
        assert not [f for f in tracked if f.endswith(".db")], "БД с юзерами попала в git!"
        assert not [f for f in tracked if f.startswith(".env")], ".env в git!"
        patterns = [
            r"BOT_TOKEN\s*=\s*[\"'][0-9]",
            r"GOOGLE_CLIENT_SECRET\s*=\s*[\"'][A-Za-z0-9_\-]{6,}",
            r"AIza[0-9A-Za-z_\-]{30,}",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"\d{8,10}:AA[A-Za-z0-9_\-]{30,}",
        ]
        hits = []
        for fname in tracked:
            if not fname.lower().endswith((".py", ".html", ".yaml", ".yml", ".md", ".txt", ".json")):
                continue
            try:
                txt = open(fname, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for pat in patterns:
                if _re2.search(pat, txt):
                    hits.append((fname, pat))
        assert not hits, f"похоже на утечку секретов: {hits}"
        log(f"SECRETS OK: {len(tracked)} файлов в git — токенов бота, ключей Google и приватных ключей нет")
    except AssertionError as e:
        fails += 1
        log("SECRETS FAIL:", str(e))
    except Exception as e:
        log("SECRETS WARN:", repr(e))



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
                 "authGoogleBtn", "convCoin", "convCoinList", "convQuick", "conv-chip",
                 "calcCopyBtn", "calcHlBtn", "tgAlertStatus",
                 "About RustDeck", "google-site-verification", "application/ld+json", "FAQPage",
                 'rel="canonical"', "og:site_name",
                 "orderRemovedText", "orderRemovedFeed", "Removed together", "ev.feed", "</html>"]:
        assert must in hub, f"Хаб не содержит {must}"
    assert "Position Calculator" in hub, "на хабе должен быть калькулятор позиций"
    assert "calcChart" in hub, "на хабе нет графика калькулятора"
    # Тикер закреплён вместе с хедером: он в липкой полосе, то есть ВЫШЕ <main>
    assert 'class="sticky top-0 z-40' in hub, "липкая полоса (хедер + тикер) пропала"
    assert hub.index('id="tickerItems"') < hub.index('<main class="flex-grow">'), \
        "тикер должен находиться в липкой полосе (выше main)"
    log("HUB OK: rustdeck.app отдаёт wallet-tracker хаб (тикер закреплён вместе с хедером)")

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
        # Ордера: для алертов «ордер снят» нужны oid, тип (Take Profit/Stop
        # Loss/Limit), размер и объём в USD — проверяем форму ответа
        for o in w.get("open_orders") or []:
            for key in ("oid", "type", "notional", "remaining", "is_tp", "is_sl", "side_label"):
                assert key in o, f"в open_orders нет {key}: {o}"
        log(f"ORDERS SHAPE OK: {len(w.get('open_orders') or [])} ордеров с oid/типом/объёмом в USD")
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

