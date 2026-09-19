from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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


# ===== SEO-роуты. Отдают один и тот же файл, SEO меняется через JS =====
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


# ===== API для расчётов (на будущее: Telegram-бот, внешние интеграции) =====
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
    return HTMLResponse(
        content="User-agent: *\nAllow: /\nSitemap: /sitemap.xml",
        media_type="text/plain",
    )


@app.get("/sitemap.xml")
async def sitemap():
    paths = ["/", "/bitcoin-risk-calculator", "/bybit-calculator", "/hyperliquid-calculator"]
    urls = "".join(f"<url><loc>{p}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>" for p in paths)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return HTMLResponse(content=xml, media_type="application/xml")