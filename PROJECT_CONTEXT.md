# КОНТЕКСТ ПРОЕКТА: RustDeck (бывш. RiskCalc) — trading tools для крипто-трейдеров

> Скопируй этот файл в любой ИИ, чтобы мгновенно восстановить контекст.

## ЧТО ЭТО
RustDeck — набор бесплатных инструментов для крипто-трейдеров. Первый инструмент —
калькулятор размера позиции и риска (при срабатывании стопа трейдер теряет ровно
заложенную сумму: $5, $10, $50, 1% и т.д.).

Домены (план):
- **rustdeck.app** (корень) — главная-хаб экосистемы (app/index.html)
- **calc.rustdeck.app** — калькулятор (продакшн, живой)
- **trade.rustdeck.app** — будущий трекер сделок / статистика кошелька
- **blog.rustdeck.app** — будущий блог
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
  он блокирует датацентр-IP), /watch 0x… (СЛЕЖЕНИЕ ЗА КОШЕЛЬКОМ 24/7: фоновый поток
  опрашивает HL раз в 60с, шлёт в TG: открытие/закрытие позиции, ликвидация, любые филлы),
  /watching, /unwatch, /status, /help. Все сообщения на английском.
  Привязка сайт↔бот через 6-значный код (15 мин). БД SQLite (эфемерная на Render!):
  subscribers (tier trial 7 дней — ОДИН триал на аккаунт, повторные линки НЕ продлевают,
  watched_wallet). Watcher работает с ЛЮБЫМ кошельком HL, доступ по подписке.
- ГЛАВНАЯ rustdeck.app (app/index.html) — рабочее приложение, НЕ лендинг:
  * WALLET TRACKER (главная фича): вводишь любой HL-адрес → account value, unrealized/realized
    PnL, win rate, открытые позиции (liq distance!), последние 12 сделок. Бэкенд
    /api/wallet/{address} через публичный API Hyperliquid (clearinghouseState + userFills),
    кэш 30с. Валидация адреса 0x+40hex.
  * LIVE MARKETS: таблица 12 монет (цена, 24h%, тренд-бар), /api/prices
  * FUNDING RATES: топ-8highest/lowest по ставке HL, /api/funding.
    ВАЖНО: predictedFundings возвращает список кортежей [coin, [[exchange, {...}],...]],
    биржа называется "HlPerp", fundingRate за fundingIntervalHours!
  * Telegram CTA + roadmap-плитки. Калькулятор с главной УБРАН (не мейн-функция,
    живёт тихо на calc.rustdeck.app)
  * LIVE ALERTS (браузер): тумблер запрашивает разрешение на Notifications, звук через
    WebAudio (низкий тон = убыток/ликвидация), тумблеры событий (open/close/liquidation/
    any fill/sound), лента событий. Опрос /api/wallet каждые 25с, diff состояний.
    Настройки в localStorage профиля.
  * ЛОКАЛЬНЫЙ ПРОФИЛЬ: Sign in в шапке (имя + сохранённые кошельки + lastWallet,
    всё в localStorage rustdeck_profile_v1, БЕЗ серверной авторизации). Чипы
    сохранённых кошельков над формой (клик = отследить, ✕ = удалить), кнопка ＋ Save wallet.
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
- Host-роутинг: ОДИН сервис riskcalc.onrender.com: rustdeck.app → хаб,
  calc.rustdeck.app/localhost → калькулятор
- smoke_test.py — все роуты, host-роутинг, /api/wallet, /api/funding, /api/markets,
  /api/fills, /api/leaderboard, /api/whales, логика одного-триала, watch ×5

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
- Google Search Console: riskcalc.onrender.com подтверждён; calc.rustdeck.app — подтвердить
  (meta google-site-verification в index.html)
- Один HTML на все страницы, title/description подменяются JS по pathname (объект SEO)
- Т ЦА: крипто-трейдеры США, фьючерсы Hyperliquid/Bybit/Binance, плечо 5–50x

## ПЛАН (roadmap)
1. ✅ Ребрендинг + чистка UI + живой тикер + новые SEO-страницы
2. ✅ Telegram-бот @RustDeckcryptobot (привязка кодом, trial 7 дней, /prices)
3. ✅ Главная-хаб rustdeck.app (app/index.html): карточки инструментов, ссылки на поддомены
4. ✅ Host-роутинг: ОДИН сервис riskcalc.onrender.com отдаёт и хаб, и калькулятор
   (main.py смотрит на Host-заголовок: rustdeck.app → хаб, calc./localhost → калькулятор)
5. 🔄 Домены: Render Dashboard → сервис → Settings → Custom Domains → добавить
   rustdeck.app и www.rustdeck.app (calc.rustdeck.app уже добавлен).
   DNS Porkbun: ALIAS корень → riskcalc.onrender.com; CNAME calc → riskcalc.onrender.com
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
