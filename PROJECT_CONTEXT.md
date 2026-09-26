# КОНТЕКСТ ПРОЕКТА: RustDeck (бывш. RiskCalc) — trading tools для крипто-трейдеров

> Скопируй этот файл в любой ИИ, чтобы мгновенно восстановить контекст.

## ЧТО ЭТО
RustDeck — набор бесплатных инструментов для крипто-трейдеров. Первый инструмент —
калькулятор размера позиции и риска (при срабатывании стопа трейдер теряет ровно
заложенную сумму: $5, $10, $50, 1% и т.д.).

Домены (free-план Render: максимум 2 кастомных домена):
- **rustdeck.app** (корень) — главная-хаб экосистемы (app/index.html) + встроенный
  калькулятор позиций; легаси-калькулятор живёт на SEO-путях того же домена
  (/bitcoin-risk-calculator, /hyperliquid-calculator и т.д.)
- **api.rustdeck.app** — те же JSON-роуты (/api/*) для внешних интеграций
- calc.rustdeck.app — ВЫВЕДЕН (домен в Render и CNAME в Porkbun удалены)
- trade.rustdeck.app / blog.rustdeck.app — будущие проекты (понадобится апгрейд плана)
- riskcalc.onrender.com — старый адрес, живой
- Репозиторий: https://github.com/timvasekin-create/riskcalc
- Хостинг: Render (Free), DNS: Porkbun → Cloudflare → Render, UptimeRobot пингует каждые 5 мин

## СТЕК
- Backend: Python 3.11+, FastAPI, uvicorn (без внешних зависимостей для API цен — urllib)
- Frontend: HTML5 + Tailwind (CDN) + vanilla JS, Jinja2 фактически не используется
- QR: qrcode-generator (CDN), шрифт Inter
- Всё бесплатно: никаких платных сервисов, никакой рекламы

## СТРУКТУРА
riskcalc/
├── main.py               # FastAPI: SEO-роуты, /api/calculate, /api/prices, robots, sitemap
├── requirements.txt      # fastapi, uvicorn[standard], pydantic
├── render.yaml           # Blueprint: calc (python) + app-хаб (статика) на бесплатном плане
├── PROJECT_CONTEXT.md    # этот файл
├── templates/
│   └── index.html        # калькулятор: весь фронтенд HTML + JS + CSS
└── app/
    └── index.html        # ГЛАВНАЯ rustdeck.app: хаб экосистемы (карточки инструментов,
                          #   живой тикер, roadmap) — ссылки на поддомены

## РЕБРЕНДИНГ (сделан)
Бренд: **RustDeck** (логотип: молния + "Rust**Deck**"), стиль Hyperliquid сохранён:
фон #0a0e13, бирюза #50d2c1, красный #f6465d, шрифт Inter.

## ЧТО УЖЕ СДЕЛАНО
- Ребрендинг RiskCalc → RustDeck; весь пользовательский текст — ТОЛЬКО на английском
  (в т.ч. бот; комментарии в коде остаются на русском)
- Убраны декоративные кнопки калькулятора: Cross/Isolated/Unified, Market/Limit/Pro,
  Save State, Available to Trade; футер калькулятора почищен (только Home + Telegram Bot)
- Живой тикер и живые цены: /api/prices (Binance + Hyperliquid, кэш 30с, все 12 монет)
- Живая привязка цен: Entry Price автозаполняется ценой выбранного актива
- Telegram-бот @RustDeckcryptobot: /start, /link <код>, /prices (Binance+HL, БЕЗ CoinGecko —
  он блокирует датацентр-IP), /watch 0x… (СЛЕЖЕНИЕ ЗА КОШЕЛЬКАМИ 24/7: МУЛЬТИ до 5 адресов
  на аккаунт, фоновый поток опрашивает HL раз в 60с, шлёт в TG: открытие/закрытие позиции,
  ликвидация, любые филлы; дедупликация — один опрос HL на адрес, даже если за ним следят
  несколько юзеров), /watching (список N/5), /unwatch [0x…] (одного или всех), /status,
  /help. Все сообщения на английском. Числа в уведомлениях: _fmt_small (6 значащих
  цифр — 0.004362, не 0.0044; >=1000 с запятыми). Watcher также видит ЛИМИТКИ/TP/SL
  (frontendOpenOrders в снапшоте): "🧾 Limit order / Stop/TP order / 🗑 Order removed".
  ЦЕНОВЫЕ АЛЕРТЫ: /alert BTC > 90000 (и <), /alerts (список с текущей ценой),
  /delalert N, /clearalerts; лимит 10 на аккаунт; проверка в общем цикле (allMids
  одним запросом на всех). Привязка сайт↔бот через 6-значный код (15 мин):
  профиль на хабе → "🔑 Create code" (+email) → юзер пишет /link КОД боту.
  АВТОРИЗАЦИЯ = EMAIL: аккаунт-ID из профиля (без пароля и без писем — экономия RAM).
  ОДИН ТРИАЛ на Telegram И один на email (повторный email на другом чате → tier=free).
  Deep-link /start watch_0x… (кнопки 👁 в Whale Feed / лидерборде, "✈️ Watch in TG"
  у трекера) — сразу запускает слежение. БД SQLite (эфемерная на Render!):
  subscribers (email, watched_wallet = адреса через запятую, tier trial 7 дней).
- ГЛАВНАЯ rustdeck.app (app/index.html) — рабочее приложение, НЕ лендинг:
  * WALLET TRACKER (главная фича): вводишь любой HL-адрес → account value, unrealized/realized
    PnL, win rate, открытые позиции (liq distance!), последние 12 сделок. Бэкенд
    /api/wallet/{address} через публичный API Hyperliquid (clearinghouseState + userFills),
    кэш 30с. Валидация адреса 0x+40hex.
  * LIVE MARKETS: таблица 12 монет (цена, 24h%, тренд-бар), /api/prices
  * FUNDING RATES: топ-8highest/lowest по ставке HL, /api/funding.
    ВАЖНО: predictedFundings возвращает список кортежей [coin, [[exchange, {...}],...]],
    биржа называется "HlPerp", fundingRate за fundingIntervalHours!
  * Telegram CTA + roadmap-плитки. Тогда калькулятор с главной убирали — теперь он
    вернулся встроенным в хаб (#calc), а легаси-калькулятор живёт на SEO-путях
    rustdeck.app (calc.* выведен из DNS)
  * LIVE ALERTS (браузер): тумблер запрашивает разрешение на Notifications, звук через
    WebAudio (низкий тон = убыток/ликвидация), тумблеры событий (open/close/liquidation/
    any fill/sound), лента событий. Опрос /api/wallet каждые 25с, diff состояний.
    Настройки в localStorage профиля.
  * ЛОКАЛЬНЫЙ ПРОФИЛЬ / АВТОРИЗАЦИЯ (email): Sign in = email (валидируется) + имя. Кнопка
    профиля в шапке → панель: email, "✈️ Open bot" + "🔑 Create code"
    (POST /api/tg/link/start с email), код + Copy + "Open Bot & Confirm" (deep-link ?start=КОД)
    + автоопрос статуса каждые 3с → "✅ Linked". Кошельки/алерты/lastWallet — в localStorage
    rustdeck_profile_v1. Клик вне панели закрывает её.
  * RUSTDECK SCORE: карточка с грейдом (S/A/B/C/D) и 0-100 очков (win rate 30 + profit
    factor 25 + avg win/loss 20 + просадка 25), серии побед/поражений, max drawdown USD.
    Кнопки 👁 (follow в TG) в Whale Feed и лидерборде; "✈️ Watch in TG" у трекера (deep-link).
  * ГРАФИК: вкладки Account Value / PNL / Perps PNL (плавные кривые, не ступеньки) +
    периоды 24H/7D/30D/All; курсор даёт crosshair и значения (дата + сумма). Account Value —
    accountValueHistory из API portfolio, Perps PNL — кумулятив закрытых филлов.
  * КАЛЬКУЛЯТОР ПОЗИЦИЙ (#calc): тикер в любом регистре (btc/ETH/hype) → свечи /api/candles,
    линии Entry/SL/TP/LIQ + текущая цена (как на HL), hover — OHLC. Решатель: плечо ОБЯЗАТЕЛЬНО
    (слайдер до 40x, пресеты 1x/3x/5x/10x), риск $/%, R:R (1:2/1:3/1:4). Любые достаточные
    данные → всё остальное: тейк из R:R, стоп из риска, капитал из риска+стопа, риск из
    капитала+стопа. Комиссии 0.045%×2. Выводы: размер, кол-во, маржа, риск, ликвидация,
    R:R, PnL при TP/SL.
  * АВТОРИЗАЦИЯ: модалка "Sign in to RustDeck" в стиле «Continue with» — аккаунт-пикер
    ("Continue as @user"), "Continue with Telegram" (код + deep-link + автоопрос),
    "Use email instead". tg_username/chat_id из /api/tg/link/status сохраняются в профиль.
  * Кошелёк аккаунта: при трекинге сразу сохраняется в аккаунт (первый = ★ default, чип с ★),
    кнопка "＋ Set your wallet" в панели; ★-кошелёк подставляется при входе.
  * Боковые баннеры (≥1500px): реф Hyperliquid, How it works, TG-бот, /score.
  * Фавикон RustDeck (тёмный квадрат, "R" + молния) на хабе и калькуляторе.
  * Алерты: тумблер "Limit orders (placed/removed)"; fmtPrice не теряет мелкие цифры
    (0.004362 вместо 0.0044).
  * OPEN ORDERS: таблица лимиток/TP/SL (frontendOpenOrders) в результатах трекера.
  * EXPORT CSV: кнопка у Recent Trades → /api/fills/{address}?limit=500 → скачивание.
  * WHALE FEED: лента сделок >= $250K у топ-40 китов HL за 24ч (бейджи OPEN/WIN/LOSS/LIQ),
    клик по строке → трекер этого кошелька. Бэкенд /api/whales: китовый universe из
    лидерборда (кэш 30 мин), обновление ФОНОВЫМ ПОТОКОМ раз в 60с (40 HL-запросов
    нельзя делать в веб-запросе), API отвечает мгновенно из кэша (или status=warming).
  * LEADERBOARD: топ-20 трейдеров по PnL за 24H/7D/All (официальный рейтинг HL,
    windowPerformances — список кортежей!), кнопка Track → трекер. /api/leaderboard
    с ретраями (файл ~10MB) и кэшем 5 мин.
  * Бот /top: топ-5 трейдеров дня с медалями, адреса копируются → /watch.
  * PnL-периоды (24H/7D/30D/All) — из API portfolio (кумулятивный pnlHistory,
    последний минус первый) — сверено с эксплорером HL ($23.47 all-time).
    Фолбэк — сумма по филлам.
  * PnL-ГРАФИК: SVG-полилиния из pnl_charts{period} (кумулятив), тумблеры 24H/7D/30D/All,
    зелёный/красный по знаку; карточка скрывается, если < 2 точек данных.
  * WIN RATE карточка: + Profit Factor (∞ когда нет убытков) и Avg win / Avg loss.
- Host-роутинг: ОДИН сервис riskcalc.onrender.com: rustdeck.app → хаб,
  localhost/onrender/calc.* (dev-фолбэк) → легаси-калькулятор
- /score/{address} — публичная карточка RustDeck Score (noindex + OG-теги) для шаринга в TG/X
- smoke_test.py — все роуты, host-роутинг, /api/wallet (+pnl_charts/value_charts/perp_chart/
  profit_factor/score/streaks/max_drawdown_usd), /api/funding, /api/markets, /api/fills,
  /api/assets, /api/candles (btc lowercase + 404 на notacoin), /score-страница,
  /api/leaderboard, /api/whales, логика одного-триала, link status (tg_username),
  _fmt_small (0.004362), watch ×5, мульти-кошельки (2 адреса, дубликаты, /unwatch 0x,
  лимит 5), email-триал (один на email), deep-link /start watch_0x…, /alert+ /delalert

## TELEGRAM-БОТ (сделано)
- @RustDeckcryptobot — работает ВНУТРИ FastAPI (фоновый поток long polling,
  отдельный воркер Render не нужен). Файл bot.py, только stdlib.
- Команды бота: /start [код], /link <код>, /prices (живые цены), /status, /help
- Привязка сайт↔бот через 6-значный код (живёт 15 мин):
  сайт POST /api/tg/link/start → код + deep-link → юзер жмёт кнопку/шлёт /link КОд
  → сайт опрашивает GET /api/tg/link/status/{code} → linked
- Подписка: при привязке пишется tier='trial', expires_at = +7 дней (тариф-фундамент)
- БД: SQLite rustdeck.db (git-игнор). ВНИМАНИЕ: диск Render эфемерный —
  при деплое база сбрасывается; позже переехать на постоянное хранилище.
- Токен бота: ТОЛЬКО через переменную окружения BOT_TOKEN (Render Dashboard),
  в git НЕ коммитить. Frontend: карточка «Telegram Alerts» в сайдбаре с кнопкой
  Connect Telegram → код → «Open Bot & Confirm» → автоопрос статуса.

## ИДЕИ НА БУДУЩЕЕ (обсудить)
- Стартовый экран решён: корень rustdeck.app → хаб (папка app/). Нейминг поддоменов
  гибкий: app./start./hub. — DNS меняется за минуту.
- Смена доменов: калькулятор — не «мейн функция», весь проект развивается как
  экосистема trading tools под брендом RustDeck.

## ЛОГИКА РАСЧЁТА (НЕ МЕНЯТЬ БЕЗ ТЕСТОВ — РАБОТАЕТ)
Вход: own_margin, risk (в $ или %), entry, stop_loss (может быть авторассчитан),
take_profit (может быть авторассчитан по R:R), leverage 1–100, long/short.
- risk_amount = margin * risk%/100 (или $ напрямую)
- stop_distance_pct = |entry − SL| / entry × 100
- position_size = margin × leverage; quantity = position_size / entry
- Авторасчёт SL: stop_distance_pct = risk_amount / position_size × 100
- Авторасчёт TP: tp_distance = stop_distance × R:R
- Ликвидация (изолированная): long → entry × (1 − 1/lev); short → entry × (1 + 1/lev)
- Комиссии: position_size × 0.00045 × 2; breakeven = entry ± fees/quantity
- R:R = tp_distance / stop_distance; net_profit = potential_profit − fees

## МОНЕТИЗАЦИЯ (без рекламы)
1. Рефералка Hyperliquid: https://app.hyperliquid.xyz/join/BENGALCAT4
   (топ-баннер, карточка в сайдбаре, кнопка под результатами, футер)
   Экономика: 10% комиссий реферала навсегда + скидка 5% пользователю
2. Донаты: USDT ERC-20 0x6F97071B375D35Af144FC0cB8AD832322393Ae12,
   BTC bc1qdcczgt55v9lqcdmc3pxdm40tgqm2psqmtq3nft (QR-модалка)

## SEO
- Google Search Console: riskcalc.onrender.com подтверждён; rustdeck.app — подтвердить
  (meta google-site-verification в index.html; canonical теперь всегда на rustdeck.app)
- Один HTML на все страницы, title/description подменяются JS по pathname (объект SEO)
- Т ЦА: крипто-трейдеры США, фьючерсы Hyperliquid/Bybit/Binance, плечо 5–50x

## ПЛАН (roadmap)
1. ✅ Ребрендинг + чистка UI + живой тикер + новые SEO-страницы
2. ✅ Telegram-бот @RustDeckcryptobot (привязка кодом, trial 7 дней, /prices)
3. ✅ Главная-хаб rustdeck.app (app/index.html): карточки инструментов, ссылки на поддомены
4. ✅ Host-роутинг: ОДИН сервис riskcalc.onrender.com отдаёт и хаб, и калькулятор
   (main.py смотрит на Host-заголовок: rustdeck.app → хаб, calc./localhost → калькулятор)
5. 🔄 Домены: rustdeck.app + api.rustdeck.app (лимит free-плана — 2 кастомных домена).
   calc.rustdeck.app УБРАН (домен в Render + CNAME в Porkbun удалены).
   DNS Porkbun: ALIAS корень → riskcalc.onrender.com; CNAME api → riskcalc.onrender.com
   (A-запись НЕ использовать — у Render нет статического IP; Blueprint НЕ нужен)
6. trade.rustdeck.app — трекер сделок по кошельку Hyperliquid (публичный API HL,
   чтение fill'ов: win rate, avg R:R, история) + бэкенд /api/wallet/{address}.
   Монетизация: Free (просмотр любого кошелька) / Pro (сохранённые кошельки, алерты)
7. Уведомления бота о сделках (интеграция логики hl_bot с D:\hl_bot)
8. Блог blog.rustdeck.app со статьями (пози сайзинг, ликвидации, R:R психология)
9. Калькулятор-апгрейд (отличие от конкурентов): fee-пресеты бирж, funding rate,
   режим «сделка недели» и т.д.
10. Индексация Google: GSC ресурс домена *.rustdeck.app через DNS TXT + sitemap

## ЭКОСИСТЕМА ФУНКЦИЙ RUSTDECK (что показываем на главной)
LIVE:      Risk Calculator (calc.) — авторасчёт SL/TP, ликвидация, живые цены
LIVE BETA: Telegram Bot (@RustDeckcryptobot) — /prices, привязка, trial
IN DEV:    Wallet Tracker (trade.) — win rate, PnL, история, watch трейдеров
SOON:      Price & Position Alerts (через бота) — уровни цены, близость ликвидации
PLANNED:   Blog (blog.) — гайды по риску
COMING:    RustDeck Pro — сохранённые кошельки, unlimited alerts, журнал сделок
ROADMAP:   мульти-биржи (Bybit/Binance fees), funding checker, trade journal,
           copy-trade watch

## ПРАВИЛА РАБОТЫ
- Тестировать локально перед push (uvicorn main:app --reload → 127.0.0.1:8000)
- 1 коммит = 1 фича; комментарии в коде — на русском
- Не ломать: HEAD-обработчик, слайдер, авторасчёт SL/TP, QR-модалку, реф-ссылку,
  SEO-подмену, sitemap, localStorage, Copy Results, все SEO-страницы
- Мобильная адаптивность обязательна (половина трафика с телефонов)
- Только бесплатные решения, без рекламы

## ОБНОВЛЕНИЕ 26.09.2026 (UI-полировка + авторизация + TG-алерты)
- Калькулятор на хабе компактнее: кнопка «Calculate Position» маленькая и внутри левой
  колонки (раньше она вылезала блоком через битую вложенность div'ов), карточки
  результатов — stat-card-sm, график на всю ширину под сеткой.
- Entry подставляет цену ИМЕННО выбранного тикера (btc→eth пересчитывает), пока поле
  не поправили руками (`calcEntryAuto`). После Calculate посчитанные поля подставляются
  в инпуты и подсвечиваются янтарным (`auto-filled`) — видно, чего не хватало.
- Short — красная подсветка (как Sell/Short на Hyperliquid): `.dir-btn.active-short`.
- Хедер sticky (`sticky top-0 z-40 bg-hyper-bg/95 backdrop-blur`) — навигация не уезжает;
  `section { scroll-margin-top:92px }`, чтобы якоря не прятались под хедер.
- График калькулятора обновляется каждые 5 сек, только при активной вкладке
  (visibilitychange). Сервер: CANDLE_TTL=5с, CANDLE_MAX=16 записей, вытеснение самой
  старой (было clear() всего кэша), SVG на клиенте не перерисовывается без изменений.
- НОВОЕ: Converter на хабе (#converter) — крипта ⇄ USD по живым ценам /api/prices,
  переключение направления кнопкой ⇄.
- НОВОЕ: «📋 Copy summary» — готовый текст плана позиции для Telegram; «Trade on HL →»
  ведёт на app.hyperliquid.xyz/trade/{COIN}.
- dev-утилита: `python js_check.py` — проверка синтаксиса всех inline-<script> в
  app/index.html и templates/index.html через `node --check` (результат в js_check_out.txt).
- TELEGRAM-АЛЕРТЫ: бот больше не теряет сообщения молча — send_message повторяет отправку
  без Markdown, если Telegram отклонил разметку. Мост «сайт → TG»: при включении Live
  Alerts хаб вызывает POST /api/tg/watch {chat_id, wallet} → bot.add_watch() добавляет
  кошелёк в слежение (лимит 5, дедуп) и бот пишет «👀 Website: now watching…».
- Сторона сделки больше не «A/B» (в HL это Ask/Bid): API отдаёт side_label
  (A → Short, B → Long), лента/таблицы/CSV на сайте и сообщения бота показывают Long/Short.
- Бот: WATCH_INTERVAL = 20 секунд вместо 60 — уведомления приходят почти мгновенно
  (в /watch текст «Checks every {WATCH_INTERVAL} seconds»).
- АВТОРИЗАЦИЯ: Google OAuth 2.0 (stdlib): /auth/google → Google → /auth/google/callback
  → cookie `rd_session` (HMAC-подпись, 30 дней) → GET /api/me отдаёт email/имя и
  привязанный TG. POST /api/logout. Кнопка «Continue with Google» в auth-модалке;
  Telegram можно привязать в любой момент из профиля («🔑 Create code») — после /link
  профиль сам подтянет chat_id (@/api/me) и включит DM-алерты.

## КАК НАСТРОИТЬ GOOGLE SIGN-IN (5 минут)
1. console.cloud.google.com → New Project (rustdeck) → APIs & Services → OAuth consent
   screen: External, название RustDeck, support email; Scopes: только
   `openid`, `email`, `profile` (пользовательские данные не запрашиваем).
2. Credentials → Create credentials → OAuth client ID → Web application.
   Authorized redirect URIs: `https://rustdeck.app/auth/google/callback`
   (для локальной отладки добавь `http://127.0.0.1:8000/auth/google/callback`).
3. Render Dashboard → сервис rustdeck-calc → Environment → добавить
   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_SECRET (любая длинная случайная строка,
   напр. `openssl rand -hex 32`) → Save (сервис передеплоится).
4. Без ключей кнопка не ломает сайт: /auth/google вернёт на `/?google=unavailable`
   и покажет тост «Google sign-in not enabled».

## КАК НАСТРОИТЬ api.rustdeck.app
1. Render Dashboard → сервис rustdeck-calc → Settings → Custom Domains → Add:
   вписать `api.rustdeck.app` → Render покажет, что нужен CNAME.
2. Porkbun → DNS Records → Add: Type `CNAME`, Host `api`,
   Answer `riskcalc.onrender.com`, TTL 600 → Save.
3. Через 5–30 минут Render выпустит TLS. Проверка: `https://api.rustdeck.app/api/prices`
   и `https://api.rustdeck.app/api/me` (тот же сервис + Host-роутинг в main.py).
   Позже, при желании, api-домен можно переключить на отдельный JSON-роутер
   (CORS-заголовки добавить в main.py middleware'ом).
