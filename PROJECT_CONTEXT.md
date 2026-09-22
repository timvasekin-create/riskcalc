# КОНТЕКСТ ПРОЕКТА: RustDeck (бывш. RiskCalc) — trading tools для крипто-трейдеров

> Скопируй этот файл в любой ИИ, чтобы мгновенно восстановить контекст.

## ЧТО ЭТО
RustDeck — набор бесплатных инструментов для крипто-трейдеров. Первый инструмент —
калькулятор размера позиции и риска (при срабатывании стопа трейдер теряет ровно
заложенную сумму: $5, $10, $50, 1% и т.д.).

Домены (план):
- **calc.rustdeck.app** — калькулятор (продакшн, живой)
- **about.rustdeck.app** — промо-лендинг RustDeck (в разработке)
- **trade.rustdeck.app** — будущий трекер сделок / статистика кошелька
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
├── render.yaml           # Blueprint: 2 сервиса (calc + about) на бесплатном плане
├── PROJECT_CONTEXT.md    # этот файл
├── templates/
│   └── index.html        # весь фронтенд: HTML + JS + CSS (~1200 строк)
└── about/
    └── index.html        # промо-лендинг RustDeck для about.rustdeck.app (статика)

## РЕБРЕНДИНГ (сделан)
Бренд: **RustDeck** (логотип: молния + "Rust**Deck**"), стиль Hyperliquid сохранён:
фон #0a0e13, бирюза #50d2c1, красный #f6465d, шрифт Inter.

## ЧТО УЖЕ СДЕЛАНО
- Ребрендинг RiskCalc → RustDeck (шапка, футер, title, Copy Results)
- Убраны декоративные кнопки: Cross/Isolated/Unified, табы Market/Limit/Pro,
  кнопка Save State (сохранение в localStorage работает автоматически каждые 5 сек),
  строка Available to Trade (дублировала Own Margin)
- Живой тикер топ-5 монет (BTC, ETH, SOL, HYPE, BNB): цена + 24h%,
  обновление каждые 30 сек. Источник: свой бэкенд /api/prices (кэш 30 сек),
  фолбэк — прямые запросы к Binance и Hyperliquid API из браузера
- /api/prices — серверный прокси к Binance + Hyperliquid (без ключей, бесплатно),
  отдаёт ВСЕ 12 монет из списка активов (BTC, ETH, SOL, HYPE, BNB, XRP, DOGE,
  LINK, AVAX, ARB, SUI, TIA), резервный источник — CoinGecko
- ЖИВАЯ ПРИВЯЗКА ЦЕН: кнопки активов показывают актуальную цену и 24h%,
  Entry Price автоматически заполняется живой ценой выбранного актива и
  обновляется каждые 30 сек. Если юзер вручную правил Entry Price — живая цена
  его не перезаписывает (флаг entryTouchedByUser); повторный клик по активу
  снова включает автопривязку. Сохранённая в localStorage цена тоже не перезаписывается.
- 4 новые SEO-страницы: /ethereum-risk-calculator, /solana-risk-calculator,
  /leverage-calculator, /liquidation-calculator (один шаблон, SEO через JS)
- Sitemap включает все 8 страниц
- render.yaml — конфиг для второго сервиса about (статика)
- about/index.html — лендинг RustDeck (hero, карточки инструментов, футер с реф-ссылкой)
- smoke_test.py — локальный тест всех роутов и API (`python smoke_test.py`)

## ИДЕИ НА БУДУЩЕЕ (обсудить)
- Стартовый экран RustDeck: возможно НЕ about.rustdeck.app, а app.rustdeck.app
  (или другой нейминг) — единая точка входа со выбором инструмента. Лендинг about/
  уже готов как основа, нейминг легко поменять.
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
2. ✅ Лендинг about.rustdeck.app (render.yaml готов, нужно добавить сервис в Render)
3. Telegram-бот (код бота лежит отдельно: D:\hl_bot — уведомления о сделках, стате, SL/TP)
4. trade.rustdeck.app — трекер сделок по кошельку Hyperliquid (публичный API HL,
   чтение fill'ов: win rate, avg R:R, история). Free-базово, премиум — потом.
5. Блог со статьями (How to Calculate Position Size on Hyperliquid и т.д.)
6. Индексация в Google (Search Console + sitemap), потом другие биржи

## ПРАВИЛА РАБОТЫ
- Тестировать локально перед push (uvicorn main:app --reload → 127.0.0.1:8000)
- 1 коммит = 1 фича; комментарии в коде — на русском
- Не ломать: HEAD-обработчик, слайдер, авторасчёт SL/TP, QR-модалку, реф-ссылку,
  SEO-подмену, sitemap, localStorage, Copy Results, все SEO-страницы
- Мобильная адаптивность обязательна (половина трафика с телефонов)
- Только бесплатные решения, без рекламы
