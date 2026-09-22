from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from typing import Optional
import os

app = FastAPI(
    title="Crypto Risk Calculator",
    description="Free position size and risk calculator for Hyperliquid, Bybit, and Bitcoin traders.",
    version="1.0.0",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "templates", "index.html")

# ===== HEAD-обработчик для Render health check =====
@app.head("/")
async def head_root():
    return Response(status_code=200)

# ===== SEO-роуты =====
@app.get("/", response_class=HTMLResponse)
async def root():
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
    # Binance: BTC, ETH, SOL, BNB — цена и 24h% одним запросом
    try:
        data = _fetch_json(
            "https://api.binance.com/api/v3/ticker/24hr?symbols=%5B%22BTCUSDT%22,%22ETHUSDT%22,%22SOLUSDT%22,%22BNBUSDT%22%5D",
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
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin&vs_currencies=usd&include_24hr_change=true",
                timeout=5.0,
            )
            cg_map = [("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL"), ("binancecoin", "BNB")]
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
                candles = _fetch_json(
                    "https://api-ui.hyperliquid.xyz/info",
                    payload={
                        "type": "candleSnapshot",
                        "req": {
                            "coin": "@107",
                            "interval": "1d",
                            "startTime": int((_time.time() - 24 * 60 * 60) * 1000),
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

    # Порядок как в тикере: BTC, ETH, SOL, HYPE, BNB
    order = {"BTC": 0, "ETH": 1, "SOL": 2, "HYPE": 3, "BNB": 4}
    result.sort(key=lambda x: order.get(x["symbol"], 99))

    data_out = {"updated": int(now), "prices": result}
    PRICE_CACHE["data"] = data_out
    PRICE_CACHE["ts"] = now
    return data_out