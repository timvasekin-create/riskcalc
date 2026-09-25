from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
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
# Корень " /": если домен rustdeck.app (корневой) — отдаём ГЛАВНУЮ-ХАБ,
# если calc.rustdeck.app / localhost / onrender — калькулятор.
# Так один сервис обслуживает оба домена, второй сервис Render не нужен.
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    host = (request.headers.get("host") or "").lower().split(":")[0]
    if host in ("rustdeck.app", "www.rustdeck.app"):
        return FileResponse(HUB_PATH)
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

    # Открытые ордера (лимитки, TP/SL, стопы)
    open_orders = []
    for o in orders or []:
        try:
            open_orders.append({
                "coin": o.get("coin"),
                "side": o.get("side"),
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
                    windows[k] = v.get("pnlHistory")

        def _period_pnl(hist):
            # pnlHistory — кумулятивный PnL: период = последний минус первый
            if not hist or len(hist) < 2:
                return None
            try:
                return round(float(hist[-1][1]) - float(hist[0][1]), 2)
            except (TypeError, ValueError, IndexError):
                return None

        pnl_periods = {
            "24h": _period_pnl(windows.get("day")),
            "7d": _period_pnl(windows.get("week")),
            "30d": _period_pnl(windows.get("month")),
            "all": _period_pnl(windows.get("allTime")),
        }
    except Exception:
        pass
    if pnl_periods.get("24h") is None:
        pnl_periods = {
            "24h": _pnl_since(closed, now * 1000 - 24 * 3600 * 1000),
            "7d": _pnl_since(closed, now * 1000 - 7 * 24 * 3600 * 1000),
            "30d": _pnl_since(closed, now * 1000 - 30 * 24 * 3600 * 1000),
            "all": round(realized, 2),
        }

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
            "best_trade": {"coin": best["coin"], "pnl": round(best["pnl"], 2)} if best else None,
            "worst_trade": {"coin": worst["coin"], "pnl": round(worst["pnl"], 2)} if worst else None,
        },
        "recent_trades": recent,
        # PnL по периодам: приоритет — API portfolio, фолбэк — сумма по филлам
        "pnl_periods": pnl_periods,
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

# ===== API: привязка Telegram =====
@app.post("/api/tg/link/start")
async def tg_link_start():
    """Сайт просит 6-значный код привязки. Юзер отправит его боту."""
    if not BOT_ENABLED:
        return JSONResponse({"error": "bot_disabled"}, status_code=503)
    code = tg_bot.create_link_code()
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