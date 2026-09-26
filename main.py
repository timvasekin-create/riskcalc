from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from typing import Optional
import os

# ===== Telegram-бот (фоновый поток, не мешает сайту) =====
import bot as tg_bot

app = FastAPI(
    title="RustDeck — Crypto Trading Tools",
    description="Free position size and risk calculator for Hyperliquid, Bybit, and Bitcoin traders.",
    version="2.0.0",
)

BOT_ENABLED = tg_bot.start_bot_thread()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "templates", "index.html")
HUB_PATH = os.path.join(BASE_DIR, "app", "index.html")

# ===== HEAD-обработчик для Render health check =====
@app.head("/")
async def head_root():
    return Response(status_code=200)

# ===== SEO-роуты =====
# Корень " /": домен rustdeck.app → ГЛАВНАЯ-ХАБ, прочие хосты (localhost,
# riskcalc.onrender.com, служебный calc.*) → легаси-калькулятор.
# calc.rustdeck.app выведен из DNS (на free-плане Render только 2 домена:
# rustdeck.app + api.rustdeck.app) — роутинг оставлен как dev-фолбэк.
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    host = (request.headers.get("host") or "").lower().split(":")[0]
    if host in ("rustdeck.app", "www.rustdeck.app"):
        return FileResponse(HUB_PATH)
    if host.startswith("api."):
        return _api_host_page(request)   # закрытая админка (только для нас, остальным 403)
    return FileResponse(INDEX_PATH)

@app.get("/bitcoin-risk-calculator", response_class=HTMLResponse)
async def bitcoin_page():
    return FileResponse(INDEX_PATH)

@app.get("/bybit-calculator", response_class=HTMLResponse)
async def bybit_page():
    return FileResponse(INDEX_PATH)

@app.get("/hyperliquid-calculator", response_class=HTMLResponse)
async def hyperliquid_page():
    return FileResponse(INDEX_PATH)

# Новые SEO-страницы (один шаблон, SEO подменяется в JS)
@app.get("/ethereum-risk-calculator", response_class=HTMLResponse)
async def ethereum_page():
    return FileResponse(INDEX_PATH)

@app.get("/solana-risk-calculator", response_class=HTMLResponse)
async def solana_page():
    return FileResponse(INDEX_PATH)

@app.get("/leverage-calculator", response_class=HTMLResponse)
async def leverage_page():
    return FileResponse(INDEX_PATH)

@app.get("/liquidation-calculator", response_class=HTMLResponse)
async def liquidation_page():
    return FileResponse(INDEX_PATH)

# ===== API =====
class CalcInput(BaseModel):
    margin: float = Field(..., gt=0)
    risk_type: str = Field(..., pattern="^(amount|percent)$")
    risk_value: float = Field(..., gt=0)
    entry: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: Optional[float] = Field(None, gt=0)
    leverage: float = Field(..., gt=0, le=100)
    direction: str = Field("long", pattern="^(long|short)$")

TAKER_FEE = 0.00045

@app.post("/api/calculate")
async def api_calculate(data: CalcInput):
    if data.entry == data.stop_loss:
        return JSONResponse({"error": "Entry and Stop Loss cannot be equal."}, status_code=400)

    risk_amount = data.margin * (data.risk_value / 100) if data.risk_type == "percent" else data.risk_value
    stop_distance_pct = abs(data.entry - data.stop_loss) / data.entry * 100
    position_size = data.margin * data.leverage
    margin_required = position_size / data.leverage
    quantity = position_size / data.entry

    tp_distance_pct = 0.0
    potential_profit = 0.0
    rr_ratio = 0.0
    if data.take_profit and data.take_profit != data.entry:
        tp_distance_pct = abs(data.take_profit - data.entry) / data.entry * 100
        potential_profit = position_size * (tp_distance_pct / 100)
        rr_ratio = tp_distance_pct / stop_distance_pct if stop_distance_pct else 0

    liq = data.entry * (1 - 1 / data.leverage) if data.direction == "long" else data.entry * (1 + 1 / data.leverage)
    total_fees = position_size * TAKER_FEE * 2
    breakeven = data.entry + total_fees / quantity if data.direction == "long" else data.entry - total_fees / quantity

    return {
        "risk_amount": round(risk_amount, 2),
        "stop_distance_pct": round(stop_distance_pct, 4),
        "tp_distance_pct": round(tp_distance_pct, 4),
        "position_size": round(position_size, 2),
        "margin_required": round(margin_required, 2),
        "quantity": round(quantity, 8),
        "potential_profit": round(potential_profit, 2),
        "rr_ratio": round(rr_ratio, 3),
        "liquidation": round(liq, 6),
        "breakeven": round(breakeven, 6),
        "total_fees": round(total_fees, 4),
        "net_profit": round(potential_profit - total_fees, 2),
    }

# ===== SEO-служебные =====
@app.get("/robots.txt")
async def robots():
    return HTMLResponse(content="User-agent: *\nAllow: /\nSitemap: /sitemap.xml", media_type="text/plain")

@app.get("/sitemap.xml")
async def sitemap(request: Request):
    base = str(request.base_url).rstrip("/")
    paths = [
        "/", 
        "/bitcoin-risk-calculator", 
        "/bybit-calculator", 
        "/hyperliquid-calculator",
        "/ethereum-risk-calculator",
        "/solana-risk-calculator",
        "/leverage-calculator",
        "/liquidation-calculator",
    ]
    urls = "".join(
        f"<url><loc>{base}{p}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>"
        for p in paths
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return HTMLResponse(content=xml, media_type="application/xml")

# ===== API: живые цены для тикера и внешних интеграций =====
# Серверный прокси к Binance + Hyperliquid (решает CORS и кэшируется на 30 сек)
import time as _time
import urllib.request
import urllib.error
import json as _json
import threading as _threading

HL_INFO_URL = "https://api-ui.hyperliquid.xyz/info"

PRICE_CACHE: dict = {"data": None, "ts": 0.0}
PRICE_TTL = 30  # секунд

def _fetch_json(url: str, payload: Optional[dict] = None, timeout: float = 5.0):
    """Мини-HTTP клиент без внешних зависимостей."""
    if payload is not None:
        req = urllib.request.Request(
            url,
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "rustdeck/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read().decode("utf-8"))

@app.get("/api/prices")
async def api_prices():
    """Живые цены топ-5 монет: BTC, ETH, SOL, HYPE, BNB. Кэш 30 сек."""
    now = _time.time()
    if PRICE_CACHE["data"] and now - PRICE_CACHE["ts"] < PRICE_TTL:
        return PRICE_CACHE["data"]

    result = []
    # Binance: все монеты списка, кроме HYPE — цена и 24h% одним запросом
    try:
        data = _fetch_json(
            "https://api.binance.com/api/v3/ticker/24hr?symbols=%5B%22BTCUSDT%22,%22ETHUSDT%22,%22SOLUSDT%22,%22BNBUSDT%22,%22XRPUSDT%22,%22DOGEUSDT%22,%22LINKUSDT%22,%22AVAXUSDT%22,%22ARBUSDT%22,%22SUIUSDT%22,%22TIAUSDT%22%5D",
            timeout=4.0,
        )
        for t in data:
            result.append({
                "symbol": t["symbol"].replace("USDT", ""),
                "price": float(t["lastPrice"]),
                "change24h": float(t["priceChangePercent"]),
            })
    except Exception:
        pass

    # Резервный источник: CoinGecko (если Binance недоступен, напр. по региону)
    if not result:
        try:
            cg = _fetch_json(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin,ripple,dogecoin,chainlink,avalanche-2,arbitrum,sui,celestia&vs_currencies=usd&include_24hr_change=true",
                timeout=5.0,
            )
            cg_map = [
                ("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL"),
                ("binancecoin", "BNB"), ("ripple", "XRP"), ("dogecoin", "DOGE"),
                ("chainlink", "LINK"), ("avalanche-2", "AVAX"), ("arbitrum", "ARB"),
                ("sui", "SUI"), ("celestia", "TIA"),
            ]
            for cg_id, sym in cg_map:
                if cg_id in cg and cg[cg_id].get("usd"):
                    change = cg[cg_id].get("usd_24h_change")
                    result.append({
                        "symbol": sym,
                        "price": float(cg[cg_id]["usd"]),
                        "change24h": round(float(change), 2) if change is not None else None,
                    })
        except Exception:
            pass

    # HYPE — только на Hyperliquid: mid-цена + 24h% из дневной свечи
    try:
        mids = _fetch_json(
            "https://api-ui.hyperliquid.xyz/info",
            payload={"type": "allMids"},
        )
        hype_mid = float(mids.get("@107", 0))  # @107 = HYPE
        if hype_mid > 0:
            change = None
            try:
                # Точное 24ч изменение: часовая свеча 24 часа назад
                # (дневная даёт открытие сегодняшнего дня — процент врёт)
                candles = _fetch_json(
                    "https://api-ui.hyperliquid.xyz/info",
                    payload={
                        "type": "candleSnapshot",
                        "req": {
                            "coin": "@107",
                            "interval": "1h",
                            "startTime": int((_time.time() - 25 * 3600) * 1000),
                            "endTime": int(_time.time() * 1000),
                        },
                    },
                )
                if isinstance(candles, list) and candles:
                    open_price = float(candles[0]["o"])
                    if open_price > 0:
                        change = (hype_mid - open_price) / open_price * 100
            except Exception:
                pass
            result.append({
                "symbol": "HYPE",
                "price": hype_mid,
                "change24h": round(change, 2) if change is not None else None,
            })
    except Exception:
        pass

    # Порядок: сначала топ-5 для тикера, потом остальные активы по списку
    order = {"BTC": 0, "ETH": 1, "SOL": 2, "HYPE": 3, "BNB": 4,
             "XRP": 5, "DOGE": 6, "LINK": 7, "AVAX": 8, "ARB": 9, "SUI": 10, "TIA": 11}
    result.sort(key=lambda x: order.get(x["symbol"], 99))

    data_out = {"updated": int(now), "prices": result}
    PRICE_CACHE["data"] = data_out
    PRICE_CACHE["ts"] = now
    return data_out

# ===== API: Wallet Tracker (Hyperliquid публичный API, без ключей) =====
import re as _re

WALLET_CACHE: dict = {}
WALLET_TTL = 5   # секунд — быстрые уведомления на вкладке (5с опрос фронта)

def _pnl_since(closed_trades: list, since_ms: float) -> float:
    """Суммарный реализованный PnL сделок, закрытых после since_ms."""
    return round(sum(t["pnl"] for t in closed_trades if t["time"] >= since_ms), 2)


def _side_label(side) -> str:
    """HL отдаёт сторону как A (ask/продажа) или B (bid/покупка) —
    в интерфейсе показываем привычные Long/Short."""
    s = (side or "").strip().upper()
    if s in ("A", "ASK", "SELL", "S"):
        return "Short"
    if s in ("B", "BID", "BUY", "L"):
        return "Long"
    return side or "—"


def _rustdeck_score(n_closed, win_rate, profit_factor, avg_win, avg_loss, base_value, max_dd):
    """RustDeck Score 0-100 и грейд S/A/B/C/D.
    win rate 30 + profit factor 25 + avg win/loss 20 + просадка 25."""
    if not n_closed:
        return None, None
    s = min(win_rate or 0, 100) / 100 * 30
    pf_capped = 3.0 if profit_factor in (None, 999.0) else min(profit_factor, 3.0)
    s += pf_capped / 3.0 * 25
    if avg_win and avg_loss:
        s += min(abs(avg_win / avg_loss), 3.0) / 3.0 * 20
    else:
        s += 10
    dd_pct = max_dd / max(base_value or 0, 100.0) * 100
    s += max(0.0, 1 - dd_pct / 50) * 25
    score = int(round(min(100.0, max(0.0, s))))
    grade = "S" if score >= 85 else "A" if score >= 70 else "B" if score >= 55 else "C" if score >= 40 else "D"
    return score, grade

def _hl_info(payload: dict, timeout: float = 10.0):
    """POST к Hyperliquid info API."""
    return _fetch_json(HL_INFO_URL, payload=payload, timeout=timeout)

@app.get("/api/wallet/{address}")
async def api_wallet(address: str):
    """Статистика любого Hyperliquid-кошелька: баланс, PnL, win rate,
    открытые позиции и последние сделки. Кэш 30 сек."""
    address = (address or "").strip()
    if not _re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        return JSONResponse({"error": "invalid_address"}, status_code=400)

    now = _time.time()
    key = address.lower()
    cached = WALLET_CACHE.get(key)
    if cached and now - cached["ts"] < WALLET_TTL:
        return cached["data"]

    try:
        state = _hl_info({"type": "clearinghouseState", "user": address})
        fills = _hl_info({"type": "userFills", "user": address})
        orders = _hl_info({"type": "frontendOpenOrders", "user": address})
        spot = _hl_info({"type": "spotClearinghouseState", "user": address})
        portfolio = _hl_info({"type": "portfolio", "user": address})
    except Exception:
        return JSONResponse({"error": "exchange_unavailable"}, status_code=502)

    margin = state.get("marginSummary") or {}
    account_value = float(margin.get("accountValue") or 0)
    margin_used = float(margin.get("totalMarginUsed") or 0)
    ntl_pos = float(margin.get("totalNtlPos") or 0)
    withdrawable = float(state.get("withdrawable") or 0)

    # Открытые позиции
    positions = []
    unrealized = 0.0
    for ap in state.get("assetPositions") or []:
        p = ap.get("position") or {}
        szi = float(p.get("szi") or 0)
        if szi == 0:
            continue
        pnl = float(p.get("unrealizedPnl") or 0)
        unrealized += pnl
        liq_raw = p.get("liquidationPx")
        entry = float(p.get("entryPx") or 0)
        distance = None
        if liq_raw and entry:
            liq = float(liq_raw)
            distance = abs(liq - float(p.get("markPx") or entry)) / float(p.get("markPx") or entry) * 100
        positions.append({
            "coin": p.get("coin"),
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "size_usd": abs(float(p.get("positionValue") or 0)),
            "entry": entry,
            "mark": float(p.get("markPx") or 0),
            "liq": float(liq_raw) if liq_raw else None,
            "liq_distance_pct": round(distance, 2) if distance is not None else None,
            "unrealized_pnl": round(pnl, 2),
            "roe_pct": round(float(p.get("returnOnEquity") or 0) * 100, 2),
            "leverage": (p.get("leverage") or {}).get("value"),
        })
    positions.sort(key=lambda x: x["size_usd"], reverse=True)

    # Статистика по закрытым сделкам
    closed = []
    for f in fills or []:
        try:
            pnl = float(f.get("closedPnl") or 0)
        except (TypeError, ValueError):
            continue
        if pnl == 0:
            continue
        closed.append({"coin": f.get("coin"), "pnl": pnl, "time": f.get("time") or 0})
    wins = sum(1 for t in closed if t["pnl"] > 0)
    realized = sum(t["pnl"] for t in closed)
    best = max(closed, key=lambda t: t["pnl"], default=None)
    worst = min(closed, key=lambda t: t["pnl"], default=None)

    # Дополнительная статистика: profit factor, средние win/loss
    wins_list = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses_list = [t["pnl"] for t in closed if t["pnl"] < 0]
    gross_win = sum(wins_list)
    gross_loss = abs(sum(losses_list))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (None if not wins_list else 999.0)
    avg_win = round(gross_win / len(wins_list), 2) if wins_list else None
    avg_loss = round(sum(losses_list) / len(losses_list), 2) if losses_list else None

    # Открытые ордера (лимитки, TP/SL, стопы)
    open_orders = []
    for o in orders or []:
        try:
            open_orders.append({
                "coin": o.get("coin"),
                "side": o.get("side"),
                "side_label": _side_label(o.get("side")),
                "size": float(o.get("origSz") or o.get("sz") or 0),
                "type": o.get("orderType") or ("Trigger" if o.get("isTrigger") else "Limit"),
                "price": float(o.get("limitPx") or o.get("triggerPx") or 0),
                "is_trigger": bool(o.get("isTrigger")),
                "reduce_only": bool(o.get("reduceOnly")),
                "time": o.get("timestamp"),
            })
        except (TypeError, ValueError):
            continue

    # Спотовые цены: token index -> имя -> mark-цена (кэш 60 сек)
    spot_cache = getattr(api_wallet, "_spot_cache", None) or {"ts": 0.0, "map": {}}
    if now - spot_cache["ts"] > 60:
        try:
            sm = _hl_info({"type": "spotMetaAndAssetCtxs"})
            meta, sctxs = sm[0], sm[1]
            tok_name = {t["index"]: t["name"] for t in meta.get("tokens", [])}
            pmap = {}
            for u, c in zip(meta.get("universe", []) or [], sctxs or []):
                try:
                    px = float(c.get("markPx") or 0)
                except (TypeError, ValueError):
                    continue
                if px <= 0 or not u.get("tokens"):
                    continue
                base = tok_name.get(u["tokens"][0])
                if base:
                    pmap[base] = px
                pmap[(u.get("name") or "").split("/")[0]] = px
            spot_cache = {"ts": now, "map": pmap}
            api_wallet._spot_cache = spot_cache
        except Exception:
            pass
    spot_price_map = spot_cache["map"]

    # Последние сделки (все типы, не только закрытия)
    recent_raw = sorted(fills or [], key=lambda f: f.get("time") or 0, reverse=True)[:12]
    recent = []
    for f in recent_raw:
        recent.append({
            "coin": f.get("coin"),
            "dir": f.get("dir"),
            "side": f.get("side"),
            "side_label": _side_label(f.get("side")),
            "px": float(f.get("px") or 0),
            "sz": float(f.get("sz") or 0),
            "closed_pnl": round(float(f.get("closedPnl") or 0), 2),
            "time": f.get("time"),
        })

    # Спот-баланс: токены оцениваем по mark-ценам спотового рынка Hyperliquid
    spot_value = 0.0
    spot_tokens = []
    for b in (spot or {}).get("balances") or []:
        try:
            total = float(b.get("total") or 0)
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        coin = b.get("coin")
        px = 1.0 if coin == "USDC" else float(spot_price_map.get(coin, 0))
        usd = total * px
        spot_value += usd
        if usd >= 0.01:
            spot_tokens.append({
                "coin": coin,
                "amount": total,
                "price": px,
                "usd": round(usd, 2),
            })
    spot_tokens.sort(key=lambda x: x["usd"], reverse=True)

    # PnL по периодам — НАПРЯМУЮ с API (portfolio, cumulative pnlHistory),
    # как в эксплорере HL. Фолбэк — сумма по филлам.
    pnl_periods = {}
    pnl_charts = {}
    value_charts = {}
    try:
        windows = {}
        for entry in portfolio or []:
            # Формат HL: [["day", {...}], ["week", {...}], ...] — но страхуемся от dict
            if isinstance(entry, dict):
                items = entry.items()
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2 and isinstance(entry[1], dict):
                items = [(entry[0], entry[1])]
            else:
                continue
            for k, v in items:
                if isinstance(v, dict):
                    windows[k] = v

        def _period_pnl(hist):
            # pnlHistory — кумулятивный PnL: период = последний минус первый
            if not hist or len(hist) < 2:
                return None
            try:
                return round(float(hist[-1][1]) - float(hist[0][1]), 2)
            except (TypeError, ValueError, IndexError):
                return None

        def _pnl_series(hist, max_points=60):
            """Кумулятивная история PnL -> [t, value]; прореживаем до max_points."""
            if not hist or len(hist) < 2:
                return []
            step = max(1, len(hist) // max_points)
            pts = hist[::step]
            if pts[-1] != hist[-1]:
                pts.append(hist[-1])
            out = []
            for p in pts:
                try:
                    out.append([int(p[0]), round(float(p[1]), 2)])
                except (TypeError, ValueError, IndexError):
                    continue
            return out

        def _hist(label):
            return ((windows.get(label) or {}).get("pnlHistory")) or []

        def _vhist(label):
            return ((windows.get(label) or {}).get("accountValueHistory")) or []

        pnl_periods = {
            "24h": _period_pnl(_hist("day")),
            "7d": _period_pnl(_hist("week")),
            "30d": _period_pnl(_hist("month")),
            "all": _period_pnl(_hist("allTime")),
        }
        # Графики по периодам (вкладки чарта: PnL / Account Value)
        for key, label in (("24h", "day"), ("7d", "week"), ("30d", "month"), ("all", "allTime")):
            series = _pnl_series(_hist(label))
            if series:
                pnl_charts[key] = series
            vseries = _pnl_series(_vhist(label))
            if vseries:
                value_charts[key] = vseries
        if not pnl_charts:
            pnl_charts = {"all": [[int(now * 1000), round(realized, 2)]]}
    except Exception:
        pass
    if pnl_periods.get("24h") is None:
        pnl_periods = {
            "24h": _pnl_since(closed, now * 1000 - 24 * 3600 * 1000),
            "7d": _pnl_since(closed, now * 1000 - 7 * 24 * 3600 * 1000),
            "30d": _pnl_since(closed, now * 1000 - 30 * 24 * 3600 * 1000),
            "all": round(realized, 2),
        }

    # Серии побед/поражений (по порядку закрытия сделок)
    closed_sorted = sorted(closed, key=lambda t: t["time"] or 0)
    best_streak = worst_streak = cur_win = cur_loss = 0
    for t in closed_sorted:
        if t["pnl"] > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        best_streak = max(best_streak, cur_win)
        worst_streak = max(worst_streak, cur_loss)

    # Кумулятивный PnL по перпам (из закрытых сделок) — вкладка "Perps PNL"
    perp_chart = []
    cum_pnl = 0.0
    for t in closed_sorted:
        cum_pnl += t["pnl"]
        perp_chart.append([int(t["time"] or 0), round(cum_pnl, 2)])
    if len(perp_chart) > 120:
        perp_chart = perp_chart[::max(1, len(perp_chart) // 120)]

    # Максимальная просадка по кумулятивной all-time кривой PnL
    max_dd = 0.0
    peak = None
    for _p in (pnl_charts or {}).get("all") or []:
        try:
            v = float(_p[1])
        except (TypeError, ValueError, IndexError):
            continue
        peak = v if peak is None else max(peak, v)
        max_dd = max(max_dd, peak - v)
    max_dd = round(max_dd, 2)

    # RustDeck Score 0-100 + грейд (общий helper — используется и на публичной странице)
    score, grade = _rustdeck_score(
        len(closed),
        wins / len(closed) * 100 if closed else None,
        profit_factor,
        avg_win,
        avg_loss,
        account_value + spot_value,
        max_dd,
    )

    data_out = {
        "address": address,
        "account_value": round(account_value, 2),
        "spot_value": round(spot_value, 2),
        "account_total": round(account_value + spot_value, 2),
        "spot_tokens": spot_tokens,
        "withdrawable": round(withdrawable, 2),
        "margin_used": round(margin_used, 2),
        "notional_position": round(ntl_pos, 2),
        "unrealized_pnl": round(unrealized, 2),
        "positions": positions,
        "open_orders": open_orders,
        "stats": {
            "closed_trades": len(closed),
            "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "realized_pnl": round(realized, 2),
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "best_trade": {"coin": best["coin"], "pnl": round(best["pnl"], 2)} if best else None,
            "worst_trade": {"coin": worst["coin"], "pnl": round(worst["pnl"], 2)} if worst else None,
            "streaks": {"best_win": best_streak, "worst_loss": worst_streak},
            "max_drawdown_usd": max_dd,
            "score": score,
            "grade": grade,
        },
        "recent_trades": recent,
        # PnL по периодам: приоритет — API portfolio, фолбэк — сумма по филлам
        "pnl_periods": pnl_periods,
        # График PnL: {period: [[t_ms, cumulative_pnl], ...]}
        "pnl_charts": pnl_charts,
        # График Account Value (equity) и кумулятивный Perps PnL (вкладки чарта)
        "value_charts": value_charts,
        "perp_chart": perp_chart,
        "updated": int(now),
    }
    WALLET_CACHE[key] = {"data": data_out, "ts": now}
    return data_out

@app.get("/api/fills/{address}")
async def api_fills(address: str, limit: int = 200):
    """Все сделки кошелька для экспорта CSV (до 500 последних)."""
    address = (address or "").strip()
    if not _re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        return JSONResponse({"error": "invalid_address"}, status_code=400)
    limit = max(1, min(limit, 500))
    try:
        fills = _hl_info({"type": "userFills", "user": address})
    except Exception:
        return JSONResponse({"error": "exchange_unavailable"}, status_code=502)

    out = []
    for f in sorted(fills or [], key=lambda x: x.get("time") or 0, reverse=True)[:limit]:
        out.append({
            "coin": f.get("coin"),
            "dir": f.get("dir"),
            "side": f.get("side"),
            "side_label": _side_label(f.get("side")),
            "px": float(f.get("px") or 0),
            "sz": float(f.get("sz") or 0),
            "closed_pnl": round(float(f.get("closedPnl") or 0), 2),
            "fee": float(f.get("fee") or 0),
            "time": f.get("time"),
        })
    return {"address": address, "count": len(out), "fills": out}

# ===== API: Market Screener (Hyperliquid: цена, объём, OI, фандинг) =====
MARKETS_CACHE: dict = {"data": None, "ts": 0.0}
MARKETS_TTL = 30  # секунд

@app.get("/api/markets")
async def api_markets():
    """Скринер перпетуалов Hyperliquid: цена, изменение 24ч, объём 24ч,
    открытый интерес (USD) и ставка фандинга. Кэш 30 сек."""
    now = _time.time()
    if MARKETS_CACHE["data"] and now - MARKETS_CACHE["ts"] < MARKETS_TTL:
        return MARKETS_CACHE["data"]

    try:
        data = _hl_info({"type": "metaAndAssetCtxs"})
    except Exception:
        return JSONResponse({"error": "exchange_unavailable"}, status_code=502)

    # Формат: [meta{universe:[{name,...}]}, ctxs[{markPx, prevDayPx, dayNtlVlm, openInterest, funding, ...}]]
    try:
        meta, ctxs = data[0], data[1]
        universe = meta.get("universe") or []
        out = []
        for i, asset in enumerate(universe):
            if i >= len(ctxs) or asset.get("isDelisted"):
                continue
            c = ctxs[i] or {}
            try:
                mark = float(c.get("markPx") or 0)
                prev = float(c.get("prevDayPx") or 0)
                oi_coins = float(c.get("openInterest") or 0)
                funding = float(c.get("funding") or 0)
                volume = float(c.get("dayNtlVlm") or 0)
            except (TypeError, ValueError):
                continue
            if mark <= 0:
                continue
            change = (mark - prev) / prev * 100 if prev > 0 else 0
            out.append({
                "coin": asset.get("name"),
                "price": mark,
                "change24h": round(change, 2),
                "volume24h": round(volume, 0),
                "open_interest_usd": round(oi_coins * mark, 0),
                "funding_1h": round(funding * 100, 4),
            })
        out.sort(key=lambda x: x["volume24h"], reverse=True)
        data_out = {"updated": int(now), "markets": out[:60]}
    except Exception:
        return JSONResponse({"error": "parse_failed"}, status_code=502)

    MARKETS_CACHE["data"] = data_out
    MARKETS_CACHE["ts"] = now
    return data_out

# ===== API: Funding Rates (Hyperliquid) =====
FUNDING_CACHE: dict = {"data": None, "ts": 0.0}
FUNDING_TTL = 60  # секунд

@app.get("/api/funding")
async def api_funding():
    """Ставки фандинга Hyperliquid по всем монетам (за 1 час + годовые %)."""
    now = _time.time()
    if FUNDING_CACHE["data"] and now - FUNDING_CACHE["ts"] < FUNDING_TTL:
        return FUNDING_CACHE["data"]

    try:
        data = _hl_info({"type": "predictedFundings"})
    except Exception:
        return JSONResponse({"error": "exchange_unavailable"}, status_code=502)

    # Формат ответа: [ [coin, [ [exchange, {fundingRate, ...}], ... ]], ... ]
    out = []
    for entry in data or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        coin = entry[0]
        for pair in entry[1] or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            exchange, info = pair[0], pair[1] or {}
            if exchange != "HlPerp":  # ставки именно Hyperliquid
                continue
            try:
                rate = float(info.get("fundingRate") or 0)  # за интервал
                interval = float(info.get("fundingIntervalHours") or 1)
            except (TypeError, ValueError):
                continue
            per_hour = rate / interval  # приводим к ставке за 1 час
            out.append({
                "coin": coin,
                "rate_1h": round(per_hour * 100, 4),        # % в час
                "rate_24h": round(per_hour * 24 * 100, 3),   # % в сутки
                "apr": round(per_hour * 24 * 365 * 100, 1),  # % годовых
            })
            break
    out.sort(key=lambda x: x["rate_1h"], reverse=True)
    data_out = {"updated": int(now), "funding": out[:40]}
    FUNDING_CACHE["data"] = data_out
    FUNDING_CACHE["ts"] = now
    return data_out

# ===== API: Leaderboard (официальный рейтинг Hyperliquid) =====
LB_CACHE: dict = {"data": None, "ts": 0.0}
LB_TTL = 300          # кэш 5 минут (файл тяжёлый, ~10MB)
LB_MIN_VALUE = 1_000  # отсекаем пустые аккаунты

@app.get("/api/leaderboard")
async def api_leaderboard():
    """Топ-трейдеры Hyperliquid по PnL за 24h / 7d / allTime (windowPerformances)."""
    now = _time.time()
    if LB_CACHE["data"] and now - LB_CACHE["ts"] < LB_TTL:
        return LB_CACHE["data"]

    try:
        last_err = None
        for _ in range(2):  # файл ~10MB, иногда обрывается — ретраим
            try:
                req = urllib.request.Request(
                    "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
                    headers={"User-Agent": "rustdeck/1.0"},
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(1)
        if last_err is not None:
            raise last_err
    except Exception:
        return JSONResponse({"error": "exchange_unavailable"}, status_code=502)

    rows = data.get("leaderboardRows") or []

    def _w(r):
        """windowPerformances -> {period: {pnl, roi, vlm}} (формат — список кортежей)."""
        out = {}
        for pair in r.get("windowPerformances") or []:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2 and isinstance(pair[1], dict):
                out[pair[0]] = pair[1]
            elif isinstance(pair, dict) and "day" in pair:
                pass
        return out

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    # day / week / allTime списки
    variants = {"day": [], "week": [], "all": []}
    for r in rows:
        addr = r.get("ethAddress") or r.get("accountAddress")
        if not addr or _f(r.get("accountValue")) < LB_MIN_VALUE:
            continue
        w = _w(r)
        name = r.get("displayName") or ""
        for key, period in (("day", "day"), ("week", "week"), ("all", "allTime")):
            p = w.get(period) or {}
            pnl = _f(p.get("pnl"))
            if pnl == 0:
                continue
            variants[key].append({
                "address": addr,
                "name": name,
                "pnl": round(pnl, 0),
                "roi": round(_f(p.get("roi")) * 100, 1),
                "vlm": round(_f(p.get("vlm")), 0),
            })

    for key in variants:
        variants[key].sort(key=lambda x: x["pnl"], reverse=True)

    data_out = {
        "updated": int(now),
        "day": variants["day"][:20],
        "week": variants["week"][:20],
        "all": variants["all"][:20],
    }
    LB_CACHE["data"] = data_out
    LB_CACHE["ts"] = now
    return data_out

# ===== API: Whale Feed (крупные сделки топ-кошельков в реальном времени) =====
WHALE_CACHE: dict = {"data": None, "ts": 0.0}
WHALE_TTL = 60          # обновление раз в минуту
WHALE_MIN_USD = 250_000 # порог «китовой» сделки
WHALE_WATCH = 40        # сколько топ-кошельков сканируем

def _whale_universe():
    """Топ-кошельки по accountValue (кэш 30 мин, leaderboard тяжёлый)."""
    uni = getattr(api_leaderboard, "_whale_uni", None) or {"ts": 0.0, "addrs": []}
    now = _time.time()
    if uni["addrs"] and now - uni["ts"] < 1800:
        return uni["addrs"]
    try:
        req = urllib.request.Request(
            "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
            headers={"User-Agent": "rustdeck/1.0"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        rows = data.get("leaderboardRows") or []

        def av(r):
            try:
                return float(r.get("accountValue") or 0)
            except (TypeError, ValueError):
                return 0.0

        top = sorted(
            (r for r in rows if r.get("ethAddress") and 100_000 <= av(r) < 500_000_000),
            key=av, reverse=True,
        )[:WHALE_WATCH]
        uni = {"ts": now, "addrs": [r["ethAddress"] for r in top]}
        api_leaderboard._whale_uni = uni
        return uni["addrs"]
    except Exception:
        return uni["addrs"]

@app.get("/api/whales")
async def api_whales():
    """Лента крупных сделок (>= $250K) топ-китов за 24ч. Мгновенно из кэша —
    обновляет фоновый поток (40 HL-запросов нельзя делать в веб-запросе)."""
    cached = WHALE_CACHE["data"]
    if cached:
        return cached
    return {"updated": int(_time.time()), "min_usd": WHALE_MIN_USD, "status": "warming", "events": []}


def _whale_refresh_loop():
    """Фоновое обновление whale-ленты раз в 60 секунд."""
    while True:
        try:
            addrs = _whale_universe()
            events = []
            now_ms = _time.time() * 1000
            for addr in addrs:
                try:
                    fills = _hl_info({"type": "userFills", "user": addr}, timeout=8)
                except Exception:
                    continue
                for f in fills or []:
                    try:
                        notional = float(f.get("px") or 0) * float(f.get("sz") or 0)
                        ftime = f.get("time") or 0
                    except (TypeError, ValueError):
                        continue
                    if notional < WHALE_MIN_USD or now_ms - ftime > 24 * 3600 * 1000:
                        continue
                    try:
                        pnl = float(f.get("closedPnl") or 0)
                    except (TypeError, ValueError):
                        pnl = 0.0
                    events.append({
                        "address": addr,
                        "coin": f.get("coin"),
                        "dir": f.get("dir"),
                        "side": f.get("side"),
                        "side_label": _side_label(f.get("side")),
                        "notional": round(notional, 0),
                        "px": float(f.get("px") or 0),
                        "pnl": round(pnl, 2),
                        "time": ftime,
                    })
            events.sort(key=lambda x: x["time"], reverse=True)
            WHALE_CACHE["data"] = {
                "updated": int(_time.time()),
                "min_usd": WHALE_MIN_USD,
                "events": events[:40],
            }
        except Exception:
            pass
        time.sleep(60)

# Фоновый поток whale-ленты (стартует вместе с сервером)
_threading.Thread(target=_whale_refresh_loop, daemon=True, name="rustdeck-whales").start()

# ===== API: свечи и активы (для встроенного калькулятора позиций) =====
ASSET_LIST_CACHE = {"ts": 0.0, "names": []}
CANDLE_CACHE = {}
CANDLE_TTL = 5    # секунд: график калькулятора обновляется каждые 5с
CANDLE_MAX = 16   # максимум монет в кэше — экономия RAM (вытесняем самую старую)

def _asset_names():
    """Названия перпов HL (кэш 10 мин)."""
    now = _time.time()
    if now - ASSET_LIST_CACHE["ts"] > 600 or not ASSET_LIST_CACHE["names"]:
        try:
            meta = _hl_info({"type": "meta"})
            names = [u.get("name") for u in meta.get("universe") or [] if u.get("name")]
            if names:
                ASSET_LIST_CACHE.update({"ts": now, "names": names})
        except Exception:
            pass
    return ASSET_LIST_CACHE["names"]

@app.get("/api/assets")
async def api_assets():
    return {"assets": _asset_names()}

@app.get("/api/candles/{coin}")
async def api_candles(coin: str, interval: str = "1h", hours: int = 72):
    """Свечи Hyperliquid: /api/candles/btc?hours=72 — регистр не важен."""
    coin = (coin or "").strip().upper()
    if not coin:
        return JSONResponse({"error": "unknown_asset"}, status_code=404)
    if not coin.startswith("@") and coin not in _asset_names():
        return JSONResponse({"error": "unknown_asset", "hint": "Use a Hyperliquid perp ticker, e.g. BTC"}, status_code=404)
    hours = max(6, min(hours, 240))
    key = f"{coin}|{interval}|{hours}"
    now = _time.time()
    cached = CANDLE_CACHE.get(key)
    if cached and now - cached["ts"] < CANDLE_TTL:
        return cached["data"]
    try:
        raw = _hl_info({
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval,
                    "startTime": int((now - hours * 3600) * 1000),
                    "endTime": int(now * 1000)},
        })
    except Exception:
        return JSONResponse({"error": "exchange_unavailable"}, status_code=502)
    candles = []
    for c in raw or []:
        try:
            candles.append({"t": c["t"], "o": float(c["o"]), "h": float(c["h"]),
                            "l": float(c["l"]), "c": float(c["c"])})
        except (TypeError, ValueError, KeyError):
            continue
    data = {"coin": coin, "interval": interval, "candles": candles, "updated": int(now)}
    CANDLE_CACHE[key] = {"data": data, "ts": now}
    # Вытесняем только самую старую запись (а не весь кэш) — плавная память
    if len(CANDLE_CACHE) > CANDLE_MAX:
        oldest = min(CANDLE_CACHE, key=lambda k: CANDLE_CACHE[k]["ts"])
        CANDLE_CACHE.pop(oldest, None)
    return data

@app.get("/score/{address}", response_class=HTMLResponse)
async def score_page(address: str):
    """Публичная карточка RustDeck Score — шарится в TG/X."""
    if not _re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        return HTMLResponse("<h1 style='font-family:sans-serif'>Invalid address</h1>", status_code=400)
    address = address.lower()
    try:
        state = _hl_info({"type": "clearinghouseState", "user": address})
        fills = _hl_info({"type": "userFills", "user": address})
        portfolio = _hl_info({"type": "portfolio", "user": address})
    except Exception:
        return HTMLResponse("<h1 style='font-family:sans-serif'>Exchange unavailable, try later</h1>", status_code=502)

    account_value = float((state.get("marginSummary") or {}).get("accountValue") or 0)
    closed = []
    for f in fills or []:
        try:
            pnl = float(f.get("closedPnl") or 0)
        except (TypeError, ValueError):
            continue
        if pnl != 0:
            closed.append({"pnl": pnl, "time": f.get("time") or 0})
    wins_list = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses_list = [t["pnl"] for t in closed if t["pnl"] < 0]
    wins = len(wins_list)
    gross_win = sum(wins_list)
    gross_loss = abs(sum(losses_list))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (None if not wins_list else 999.0)
    avg_win = round(gross_win / len(wins_list), 2) if wins_list else None
    avg_loss = round(sum(losses_list) / len(losses_list), 2) if losses_list else None
    win_rate = round(wins / len(closed) * 100, 1) if closed else None

    hist = []
    try:
        for entry in portfolio or []:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2 and entry[0] == "allTime" and isinstance(entry[1], dict):
                hist = entry[1].get("pnlHistory") or []
    except Exception:
        hist = []
    max_dd, peak = 0.0, None
    for p in hist:
        try:
            val = float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        peak = val if peak is None else max(peak, val)
        max_dd = max(max_dd, peak - val)
    max_dd = round(max_dd, 2)

    score, grade = _rustdeck_score(len(closed), win_rate, profit_factor, avg_win, avg_loss, account_value, max_dd)
    colors = {"S": "#50d2c1", "A": "#50d2c1", "B": "#f0b90b", "C": "#f0b90b", "D": "#f6465d"}
    color = colors.get(grade or "D", "#8b96a3")
    pf_s = "∞" if profit_factor == 999.0 else (profit_factor if profit_factor is not None else "—")
    wr_s = f"{win_rate}%" if win_rate is not None else "—"
    score_s = score if score is not None else "—"
    title = f"RustDeck Score: {grade or '—'} ({score_s}/100)"
    desc = f"Win rate {wr_s} · PF {pf_s} · Max drawdown ${max_dd:,.2f}. Track any Hyperliquid wallet free on RustDeck."
    favicon = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='%230a0e13'/><text x='30' y='45' font-family='Arial' font-size='36' font-weight='bold' fill='%2350d2c1' text-anchor='middle'>R</text><path d='M50 6 L38 26 h7 l-6 16 16-22 h-8z' fill='%23f0b90b'/></svg>"
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="robots" content="noindex">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<link rel="icon" href="{favicon}">
<style>
body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
       background:#0a0e13; color:#eaecef; font-family:Inter,Arial,sans-serif; }}
.card {{ background:#0f1419; border:1px solid #1e252e; border-radius:16px; padding:28px 32px;
        text-align:center; max-width:420px; width:calc(100% - 40px); }}
.label {{ font-size:10px; letter-spacing:0.2em; text-transform:uppercase; color:#5c6670; margin-bottom:14px; }}
.grade {{ width:72px; height:72px; margin:0 auto 14px; border-radius:16px; border:2px solid; display:flex;
         align-items:center; justify-content:center; font-size:36px; font-weight:800; }}
.score {{ font-size:28px; font-weight:800; }}
.score span {{ color:#5c6670; font-size:14px; font-weight:600; }}
.stats {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:20px 0 14px; }}
.stats div {{ background:#0a0e13; border:1px solid #1e252e; border-radius:10px; padding:10px; }}
.stats b {{ display:block; font-size:16px; }}
.stats span {{ font-size:10px; color:#5c6670; text-transform:uppercase; letter-spacing:0.08em; }}
.addr {{ font-family:monospace; font-size:11px; color:#5c6670; margin-bottom:16px; }}
.cta {{ display:block; background:#50d2c1; color:#0a0e13; font-weight:700; padding:12px;
       border-radius:10px; text-decoration:none; font-size:13px; }}
</style></head><body>
  <div class="card">
    <div class="grade" style="color:{color}; border-color:{color}">{grade or '—'}</div>
    <div class="score">{score_s} <span>/ 100</span></div>
    <div class="label" style="margin-top:8px">RustDeck Score</div>
    <div class="stats">
      <div><b>{wr_s}</b><span>Win rate</span></div>
      <div><b>{pf_s}</b><span>Profit factor</span></div>
      <div><b>${max_dd:,.2f}</b><span>Max drawdown</span></div>
      <div><b>${account_value:,.2f}</b><span>Account value</span></div>
    </div>
    <div class="addr">{address[:10]}…{address[-6:]}</div>
    <a class="cta" href="https://rustdeck.app">Check any wallet on RustDeck →</a>
  </div>
</body></html>"""
    return HTMLResponse(html)

# ===== API: привязка Telegram =====
@app.post("/api/tg/link/start")
async def tg_link_start(request: Request):
    """Сайт просит 6-значный код привязки. Юзер отправит его боту.
    Email берём из Google-сессии (подделать нельзя), тело запроса — фолбэк."""
    if not BOT_ENABLED:
        return JSONResponse({"error": "bot_disabled"}, status_code=503)
    email = None
    sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if sess:
        email = sess[0]
    if not email:
        try:
            body = await request.json()
            if isinstance(body, dict):
                email = (body.get("email") or "").strip().lower() or None
        except Exception:
            email = None
    code = tg_bot.create_link_code(email)
    if not code:
        return JSONResponse({"error": "code_generation_failed"}, status_code=500)
    username = tg_bot.get_bot_username()
    return {
        "code": code,
        "ttl_seconds": tg_bot.CODE_TTL,
        "bot_username": username,  # может быть None, если Telegram недоступен
        "deep_link": f"https://t.me/{username}?start={code}" if username else None,
    }

@app.get("/api/tg/link/status/{code}")
async def tg_link_status(code: str):
    """Сайт опрашивает раз в 3 сек: привязался ли юзер."""
    if not BOT_ENABLED:
        return JSONResponse({"error": "bot_disabled"}, status_code=503)
    return tg_bot.link_code_status(code)


# ===== API: мост «сайт → Telegram» =====
# Кнопка Live Alerts на хабе добавляет кошелёк в список слежения бота,
# чтобы уведомления приходили и в Telegram (если аккаунт привязан).
class TgWatchInput(BaseModel):
    chat_id: int
    wallet: str

@app.post("/api/tg/watch")
async def tg_watch(data: TgWatchInput):
    if not BOT_ENABLED:
        return JSONResponse({"error": "bot_disabled"}, status_code=503)
    return tg_bot.add_watch(data.chat_id, data.wallet)


# ===== Авторизация через Google (OAuth 2.0 Authorization Code, stdlib) =====
# Ключи задаются в Render → Environment: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
# APP_SECRET (любая длинная строка — подпись cookie-сессии).
import base64 as _b64
import hashlib as _hashlib
import hmac as _hmac
import secrets as _secrets
from urllib.parse import urlencode as _urlencode

APP_SECRET = os.environ.get("APP_SECRET", "").strip() or _secrets.token_hex(32)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
SESSION_COOKIE = "rd_session"
SESSION_TTL = 30 * 86400      # 30 дней
OAUTH_STATE_COOKIE = "rd_state"

def google_ready() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

def _sign(raw: str) -> str:
    return _hmac.new(APP_SECRET.encode(), raw.encode(), _hashlib.sha256).hexdigest()

def make_session(email: str, name: str = "") -> str:
    """Подписанный токен сессии: base64(email|name|ts|hmac)."""
    payload = f"{(email or '').strip().lower()}|{(name or '').replace('|', ' ')}|{int(_time.time())}"
    body = f"{payload}|{_sign(payload)}"
    return _b64.urlsafe_b64encode(body.encode()).decode().rstrip("=")

def read_session(token: str):
    """Проверка подписи и срока → (email, name) или None."""
    if not token:
        return None
    try:
        raw = _b64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except Exception:
        return None
    parts = raw.split("|")
    if len(parts) < 4:
        return None
    payload = "|".join(parts[:-1])
    if not _hmac.compare_digest(parts[-1], _sign(payload)):
        return None
    try:
        if _time.time() - float(parts[2]) > SESSION_TTL:
            return None
    except ValueError:
        return None
    return parts[0], parts[1]

@app.get("/auth/google")
async def auth_google(request: Request):
    """Редирект на Google. Без ключей — возвращаемся на сайт с подсказкой."""
    if not google_ready():
        return RedirectResponse(url="/?google=unavailable", status_code=302)
    base = str(request.base_url).rstrip("/")
    state = _secrets.token_urlsafe(16)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{base}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    resp = RedirectResponse(url="https://accounts.google.com/o/oauth2/v2/auth?" + _urlencode(params), status_code=302)
    resp.set_cookie(OAUTH_STATE_COOKIE, f"{state}.{_sign(state)}", max_age=600, httponly=True, samesite="lax")
    return resp


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Google вернул код — меняем на токен, достаём email и ставим cookie-сессию."""
    if error or not code:
        return RedirectResponse("/?google=failed", status_code=302)
    cookie = request.cookies.get(OAUTH_STATE_COOKIE, "")
    want_state, _, sig = cookie.partition(".")
    if not want_state or want_state != state or not _hmac.compare_digest(sig, _sign(state)):
        return RedirectResponse("/?google=failed", status_code=302)
    if not google_ready():
        return RedirectResponse("/?google=unavailable", status_code=302)

    base = str(request.base_url).rstrip("/")
    try:
        form = _urlencode({
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{base}/auth/google/callback",
            "grant_type": "authorization_code",
        }).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            tok = _json.loads(r.read().decode("utf-8"))
        id_token = tok.get("id_token") or ""
        payload_b64 = id_token.split(".")[1] if id_token.count(".") == 2 else ""
        claims = {}
        if payload_b64:
            claims = _json.loads(_b64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)).decode())
        email = (claims.get("email") or "").strip().lower()
        name = (claims.get("name") or claims.get("given_name") or "").strip()
        if not email:
            raise ValueError("no email in id_token")
    except Exception:
        return RedirectResponse("/?google=failed", status_code=302)

    resp = RedirectResponse("/?google=ok", status_code=302)
    host = (request.headers.get("host") or "").lower().split(":")[0]
    cookie_domain = ".rustdeck.app" if host.endswith("rustdeck.app") else None
    resp.set_cookie(
        SESSION_COOKIE, make_session(email, name),
        max_age=SESSION_TTL, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), domain=cookie_domain,
    )
    resp.delete_cookie(OAUTH_STATE_COOKIE, domain=cookie_domain)
    return resp

@app.get("/api/me")
async def api_me(request: Request):
    """Кто вошёл (Google-cookie) + подписка/кошелёк/TG — для профиля на хабе."""
    sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if not sess:
        return {"authenticated": False}
    email, name = sess
    st = {"email": email, "name": None, "wallet": None, "tg": None}
    if BOT_ENABLED:
        try:
            st = tg_bot.user_status(email)
        except Exception:
            pass
    return {
        "authenticated": True,
        "email": email,
        "name": st.get("name") or name or email.split("@")[0],
        "wallet": st.get("wallet"),
        "tg": st.get("tg"),   # {"chat_id", "username", "tier", "expires_at", "days_left", "active"}
        "is_admin": is_admin(email),
    }

@app.post("/api/logout")
async def api_logout(request: Request):
    resp = JSONResponse({"ok": True})
    host = (request.headers.get("host") or "").lower().split(":")[0]
    resp.delete_cookie(SESSION_COOKIE, domain=".rustdeck.app" if host.endswith("rustdeck.app") else None)
    resp.delete_cookie(SESSION_COOKIE)   # на случай host-only cookie
    return resp



# ===== API: профиль пользователя (имя + личный кошелёк) =====
class ProfileInput(BaseModel):
    name: Optional[str] = None
    wallet: Optional[str] = None

@app.get("/api/profile")
async def api_profile_get(request: Request):
    """Профиль залогиненного Google-аккаунта: имя, кошелёк, TG, подписка."""
    sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if not sess:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    email = sess[0]
    st = {"email": email, "name": None, "wallet": None, "tg": None}
    if BOT_ENABLED:
        try:
            st = tg_bot.user_status(email)
        except Exception:
            pass
    st["is_admin"] = is_admin(email)
    return st

@app.post("/api/profile")
async def api_profile_set(data: ProfileInput, request: Request):
    """Сохраняет имя/кошелёк профиля в БД (не только localStorage)."""
    sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if not sess:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    email = sess[0]
    wallet = (data.wallet or "").strip()
    if wallet and not _re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet):
        return JSONResponse({"error": "invalid_wallet"}, status_code=400)
    if not BOT_ENABLED:
        return {"ok": True, "email": email, "name": (data.name or "").strip() or None,
                "wallet": wallet.lower() or None}
    prof = tg_bot.save_profile(email, data.name, wallet or None)
    return {"ok": True, **(prof or {})}


# ===== Админка (api.rustdeck.app): только для своих =====
# Список админов: env ADMIN_EMAILS (через запятую) или владелец по умолчанию.
ADMIN_EMAILS = [e.strip().lower() for e in
                os.environ.get("ADMIN_EMAILS", "ila281510@gmail.com").split(",") if e.strip()]

def is_admin(email: str) -> bool:
    return bool(email) and email.strip().lower() in ADMIN_EMAILS

def admin_session(request: Request):
    """Сессия админа или None (обычные юзеры в админку не проходят)."""
    sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if not sess or not is_admin(sess[0]):
        return None
    return sess

def _admin_login_html(note: str = "") -> str:
    """Экран входа для api-домена: чужим — 403, свои видят кнопку Google."""
    html = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RustDeck API — restricted</title><meta name="robots" content="noindex">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
       background:#0a0e13; color:#eaecef; font-family:Inter,Arial,sans-serif; }
.card { background:#0f1419; border:1px solid #1e252e; border-radius:16px; padding:28px 32px;
        text-align:center; max-width:430px; width:calc(100% - 40px); }
h1 { font-size:18px; margin:10px 0 8px; }
p { color:#8b96a3; font-size:12px; line-height:1.65; margin:0 0 6px; }
a.btn { display:block; margin-top:16px; background:#50d2c1; color:#0a0e13; font-weight:700;
        padding:12px; border-radius:10px; text-decoration:none; font-size:13px; }
small { color:#5c6670; font-size:10px; display:block; margin-top:10px; }
.note { color:#f6465d; font-size:11px; margin-top:8px; }
</style></head><body><div class="card">
<div style="font-size:28px">🔒</div>
<h1>RustDeck API — restricted area</h1>
<p>Внутренний домен: здесь живёт админ-консоль RustDeck.<br>
Публичные данные — цены, кошельки, рынки — на <a href="https://rustdeck.app" style="color:#50d2c1">rustdeck.app</a>.</p>
NOTE_BLOCK
<a class="btn" href="/auth/google">Sign in with Google</a>
<small>Access is limited to approved accounts.</small>
</div></body></html>"""
    return html.replace("NOTE_BLOCK", note)

def _api_host_page(request: Request):
    """Страница api.rustdeck.app: админка для своих, 403 для остальных."""
    sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if sess and is_admin(sess[0]):
        return HTMLResponse(_admin_page_html(sess[0]))
    if sess:
        note = '<div class="note">Signed in as %s — no admin access for this account.</div>' % sess[0]
        return HTMLResponse(_admin_login_html(note), status_code=403)
    return HTMLResponse(_admin_login_html())


# ---- HTML админ-дашборда (api.rustdeck.app), разбит на части для читаемости ----
_ADMIN_HTML_HEAD = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RustDeck Admin — users & subscriptions</title><meta name="robots" content="noindex">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='%230a0e13'/><text x='30' y='45' font-family='Arial' font-size='36' font-weight='bold' fill='%2350d2c1' text-anchor='middle'>R</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
body { margin:0; background:#0a0e13; color:#eaecef; font-family:Inter,Arial,sans-serif; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1150px; margin:0 auto; padding:22px 16px 60px; }
.head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; flex-wrap:wrap; }
.brand { font-weight:800; font-size:18px; letter-spacing:-0.01em; }
.brand span.t { color:#50d2c1; } .brand span.m { color:#5c6670; font-weight:500; font-size:13px; }
.chip { font-size:11px; font-family:monospace; color:#8b96a3; background:#0f1419; border:1px solid #1e252e; border-radius:999px; padding:5px 11px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:16px; }
.card { background:#0f1419; border:1px solid #1e252e; border-radius:12px; padding:14px; }
.card b { display:block; font-size:22px; font-weight:800; }
.card span { font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:#5c6670; }
.bar { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
input[type=text] { flex:1; min-width:220px; background:#0a0e13; border:1px solid #1e252e; border-radius:8px;
                   padding:10px 12px; color:#eaecef; font-size:13px; font-family:monospace; }
input[type=text]:focus { outline:none; border-color:#50d2c1; box-shadow:0 0 0 3px rgba(80,210,193,0.1); }
button { cursor:pointer; font-weight:700; border-radius:8px; border:1px solid #1e252e; background:#0f1419;
         color:#eaecef; font-size:12px; padding:9px 12px; font-family:inherit; transition:border-color .15s, color .15s; }
button:hover { border-color:#50d2c1; color:#50d2c1; }
button.primary { background:#50d2c1; color:#0a0e13; border-color:#50d2c1; }
button.primary:hover { background:#3ba899; color:#0a0e13; }
table { width:100%; border-collapse:collapse; font-size:12px; background:#0f1419;
        border:1px solid #1e252e; border-radius:12px; overflow:hidden; }
th { text-align:left; padding:10px; color:#5c6670; font-size:10px; text-transform:uppercase;
     letter-spacing:.1em; border-bottom:1px solid #1e252e; font-weight:600; }
td { padding:10px; border-bottom:1px solid #151a20; vertical-align:middle; }
tr:last-child td { border-bottom:none; }
.mono { font-family:monospace; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:10px; font-weight:700; letter-spacing:.04em; }
.badge.ok { background:rgba(80,210,193,.12); color:#50d2c1; }
.badge.bad { background:rgba(246,70,93,.12); color:#f6465d; }
.muted { color:#8b96a3; } .dim { color:#5c6670; font-size:10px; }
.row { display:flex; gap:6px; flex-wrap:wrap; }
#toast { position:fixed; right:16px; bottom:16px; background:#151a20; border:1px solid #2a323d;
         border-left:3px solid #50d2c1; border-radius:10px; padding:10px 14px; font-size:12px; display:none; z-index:9; }
</style></head><body>"""

_ADMIN_HTML_BODY = """
<div class="wrap">
  <div class="head">
    <div class="brand">⚡ Rust<span class="t">Deck</span> <span class="m">admin · users &amp; subscriptions</span></div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span class="chip">%%EMAIL%%</span>
      <button onclick="load()">⟳ Refresh</button>
      <button onclick="logout()">Sign out</button>
    </div>
  </div>
  <div class="cards" id="cards"></div>
  <div class="bar">
    <input type="text" id="q" placeholder="Search: email, @telegram, wallet…" oninput="render()">
    <button class="primary" onclick="load()">Reload users</button>
  </div>
  <table><thead><tr>
    <th>User</th><th>Telegram</th><th>Wallet</th><th>Plan</th><th>Wallets / alerts</th><th>Add days</th>
  </tr></thead><tbody id="rows"><tr><td colspan="6" class="muted">Loading…</td></tr></tbody></table>
</div>
<div id="toast"></div>
<script>
let USERS = [];
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmtDate(ts){ return ts ? new Date(ts * 1000).toLocaleString('en-US', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false}) : '—'; }
function toast(msg, bad){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.borderLeftColor = bad ? '#f6465d' : '#50d2c1';
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3200);
}
async function load(){
  try {
    const r = await fetch('/admin/users');
    if (!r.ok) { toast('Access denied', true); return; }
    const j = await r.json();
    USERS = j.users || [];
    render();
  } catch(e) { toast('Network error', true); }
}
function render(){
  const q = (document.getElementById('q').value || '').toLowerCase();
  const list = USERS.filter(u => !q || [u.email, u.name, u.tg_username, u.wallet].filter(Boolean).join(' ').toLowerCase().includes(q));
  const active = USERS.filter(u => u.active).length;
  const totalAlerts = USERS.reduce((a, u) => a + (u.alerts || 0), 0);
  const totalWallets = USERS.reduce((a, u) => a + (u.wallets || 0), 0);
  document.getElementById('cards').innerHTML = [
    ['Accounts', USERS.length], ['Active subs', active], ['Expired / no TG', USERS.length - active],
    ['Watched wallets', totalWallets], ['Price alerts', totalAlerts],
  ].map(pair => '<div class="card"><b>' + pair[1] + '</b><span>' + pair[0] + '</span></div>').join('');
  document.getElementById('rows').innerHTML = list.map(u => {
    const plan = u.tier
      ? (u.active
          ? '<span class="badge ok">' + esc(u.tier) + ' · ' + u.days_left + 'd left</span>'
          : '<span class="badge bad">expired</span>')
      : '<span class="muted">no Telegram</span>';
    const until = u.expires_at ? '<div class="dim">until ' + fmtDate(u.expires_at) + '</div>' : '';
    const tg = u.tg_username ? '@' + esc(u.tg_username) : '<span class="muted">—</span>';
    const wallet = u.wallet
      ? '<span class="mono">' + esc(u.wallet.slice(0, 8)) + '…' + esc(u.wallet.slice(-4)) + '</span>'
      : '<span class="muted">—</span>';
    const actions = u.chat_id
      ? '<div class="row">' + [1, 7, 30].map(d => '<button onclick="addDays(' + u.chat_id + ',' + d + ')">+' + d + 'd</button>').join('') + '</div>'
      : '<span class="dim">link Telegram first</span>';
    return '<tr>'
      + '<td><div style="font-weight:600">' + esc(u.name || '—') + '</div>'
      + '<div class="mono muted" style="font-size:11px">' + esc(u.email || '—') + '</div></td>'
      + '<td>' + tg + '<div class="dim mono">' + (u.chat_id || '') + '</div></td>'
      + '<td>' + wallet + '</td>'
      + '<td>' + plan + until + '</td>'
      + '<td class="mono">' + (u.wallets || 0) + ' / ' + (u.alerts || 0) + '</td>'
      + '<td>' + actions + '</td>'
      + '</tr>';
  }).join('') || '<tr><td colspan="6" class="muted">No users yet</td></tr>';
}
async function addDays(chatId, days){
  try {
    const r = await fetch('/admin/add_days', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({chat_id: chatId, days: days})
    });
    const j = await r.json();
    if (j.ok) { toast('+' + days + 'd → ' + j.days_left + ' days left'); load(); }
    else { toast('Failed: ' + (j.error || '?'), true); }
  } catch(e) { toast('Network error', true); }
}
async function logout(){ await fetch('/api/logout', {method: 'POST'}).catch(() => {}); location.reload(); }
load();
setInterval(load, 60000);
</script></body></html>"""

def _admin_page_html(email: str) -> str:
    """Дашборд админки (api.rustdeck.app): юзеры, TG, кошельки, подписка, +дни."""
    return (_ADMIN_HTML_HEAD + _ADMIN_HTML_BODY).replace("%%EMAIL%%", email)


class AddDaysInput(BaseModel):
    chat_id: Optional[int] = None
    email: Optional[str] = None
    days: int = 7

@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    """Админка и на основном домене (скрытый путь): только для ADMIN_EMAILS.
    Остальным — 403, чтобы панель не была публичной."""
    sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if sess and is_admin(sess[0]):
        return HTMLResponse(_admin_page_html(sess[0]))
    note = ('<div class="note">Signed in as %s — no admin access for this account.</div>' % sess[0]
            if sess else '<div class="note">Admin access only — sign in with an approved Google account.</div>')
    return HTMLResponse(_admin_login_html(note), status_code=403)

@app.get("/admin/users")
async def admin_users(request: Request):
    """Список аккаунтов: TG-юзер, почта, кошелёк, подписка, счётчики."""
    sess = admin_session(request)
    if not sess:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    users = tg_bot.list_users() if BOT_ENABLED else []
    return {"updated": int(_time.time()), "admin": sess[0], "users": users}

@app.post("/admin/add_days")
async def admin_add_days(data: AddDaysInput, request: Request):
    """Продлить подписку: подписка привязана к TG-аккаунту (chat_id)."""
    if not admin_session(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not BOT_ENABLED:
        return JSONResponse({"error": "bot_disabled"}, status_code=503)
    return tg_bot.extend_subscription(data.chat_id, data.email, data.days)

@app.middleware("http")
async def api_host_guard(request: Request, call_next):
    """api.rustdeck.app — внутренний домен: посторонним 403,
    открыто только через Google-вход и админские пути."""
    host = (request.headers.get("host") or "").lower().split(":")[0]
    if host.startswith("api."):
        path = request.url.path
        allowed = (
            path == "/" or path == "/favicon.ico"
            or path in ("/api/me", "/api/logout")
            or path.startswith("/auth/")
            or path.startswith("/admin")
        )
        if not allowed:
            sess = read_session(request.cookies.get(SESSION_COOKIE, ""))
            if not sess or not is_admin(sess[0]):
                note = '<div class="note">Private API — admin access only.</div>'
                return HTMLResponse(_admin_login_html(note), status_code=403)
    return await call_next(request)



