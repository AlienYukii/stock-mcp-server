import os
from datetime import time as dtime
from datetime import datetime

import pandas as pd
import pytz
import requests
import yfinance as yf
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stock-prices", host="0.0.0.0")

FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "")

# ── Tokenized stock map: stock key → crypto token symbol ─────────────────────
_STOCK_TOKEN_MAP: dict[str, str] = {
    "AMZN":  "AMZNX-USD",
    "GOOGL": "GOOGLX-USD",
    "NVDA":  "NVDAX-USD",
    "INTC":  "INTCX-USD",
    "TSLA":  "TSLAX-USD",
    "AMD":   "AMDX-USD",
    "MSFT":  "MSFTX-USD",
    "SNDK":  "SNDK-USD",
    "MU":    "MUON-USD",
    "TSM":   "TSMON-USD",
    "DELL":  "DELLON-USD",
    "SPY":   "SPYON-USD",
    "SKH":   "000660-USD",   # SK Hynix tokenized
    "SMSN":  "005930-USD",   # Samsung tokenized
}

# ── Korean alias → yfinance symbol ───────────────────────────────────────────
_KR_ALIAS_MAP: dict[str, str] = {
    "SMSN": "005930.KS",  # Samsung Electronics
    "SKH":  "000660.KS",  # SK Hynix
}

# ── All display aliases → canonical ticker ────────────────────────────────────
_ALIASES: dict[str, str] = {
    # Indices
    "台灣加權": "^TWII", "加權指數": "^TWII", "台加": "^TWII", "TWII": "^TWII",
    "S&P500": "^GSPC", "SP500": "^GSPC",
    "NASDAQ": "^IXIC", "那斯達克": "^IXIC",
    "道瓊": "^DJI", "DOW": "^DJI",
    "費半": "^SOX", "SOX": "^SOX",
    # US stocks (Chinese names)
    "蘋果": "AAPL",
    "特斯拉": "TSLA",
    "輝達": "NVDA", "英偉達": "NVDA",
    "微軟": "MSFT",
    "谷歌": "GOOGL", "Alphabet": "GOOGL",
    "亞馬遜": "AMZN",
    "超微": "AMD",
    "英特爾": "INTC",
    "美光": "MU",
    "台積電": "TSM",
    "戴爾": "DELL",
    "英特爾": "INTC",
    # KR stocks
    "三星": "SMSN",
    "SK海力士": "SKH",
    "現代汽車": "005380.KS",
    "起亞": "000270.KS",
    "NAVER": "035420.KS",
    "Kakao": "035720.KS",
    "LG化學": "051910.KS",
    "POSCO": "005490.KS",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_market_open(market: str) -> bool:
    now_utc = datetime.now(pytz.UTC)
    if now_utc.weekday() >= 5:
        return False
    if market == "US":
        t = now_utc.astimezone(pytz.timezone("America/New_York")).time()
        return dtime(9, 30) <= t <= dtime(16, 0)
    if market == "KR":
        t = now_utc.astimezone(pytz.timezone("Asia/Seoul")).time()
        return dtime(9, 0) <= t <= dtime(15, 30)
    return False


def _resolve(raw: str) -> tuple[str, str | None, str]:
    """
    Returns (yfinance_symbol, token_map_key, market).
    token_map_key is the key to look up in _STOCK_TOKEN_MAP; None if not applicable.
    market: "TW" | "KR" | "US" | "INDEX"
    """
    s = _ALIASES.get(raw.strip(), raw).upper().strip()

    # KR alias (SMSN, SKH)
    if s in _KR_ALIAS_MAP:
        return _KR_ALIAS_MAP[s], s, "KR"

    # TW: 4-5 digit number
    clean_tw = s.replace(".TW", "").replace(".TWO", "")
    if clean_tw.isdigit() and len(clean_tw) in (4, 5):
        return f"{clean_tw}.TW", None, "TW"

    # KR: 6-digit number
    clean_kr = s.replace(".KS", "").replace(".KQ", "")
    if clean_kr.isdigit() and len(clean_kr) == 6:
        suffix = ".KQ" if ".KQ" in s else ".KS"
        return f"{clean_kr}{suffix}", None, "KR"

    # Index
    if s.startswith("^"):
        return s, None, "INDEX"

    # US stock
    return s, s, "US"


def _safe_float(val) -> float | None:
    try:
        v = float(val)
        return None if pd.isna(v) else round(v, 4)
    except Exception:
        return None


def _fetch_fugle(symbol: str) -> dict | None:
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{symbol}"
    try:
        resp = requests.get(url, headers={"X-API-KEY": FUGLE_API_KEY}, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        price = data.get("lastPrice") or data.get("closePrice") or data.get("referencePrice")
        if not price:
            return None
        prev = data.get("previousClose") or 0
        change_pct = round((float(price) - float(prev)) / float(prev) * 100, 2) if prev else None
        return {"symbol": symbol, "price": float(price), "change_pct": change_pct, "currency": "TWD", "source": "Fugle"}
    except Exception:
        return None


def _fetch_yf(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        is_index = symbol.startswith("^")
        hist = ticker.history(period="5d", interval="1d") if is_index else ticker.history(period="1d", interval="1m")
        if hist.empty and not is_index:
            hist = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        try:
            info = ticker.fast_info
            currency = getattr(info, "currency", None) or "USD"
            prev = getattr(info, "previous_close", None)
        except Exception:
            currency = "USD"
            prev = None
        if prev is None and len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
        change_pct = round((price - float(prev)) / float(prev) * 100, 2) if prev else None
        return {"symbol": symbol, "price": price, "change_pct": change_pct, "currency": currency, "source": "yfinance"}
    except Exception:
        return None


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """
    取得股票或指數即時報價。

    支援市場：
    - 台股：4-5 位數字（如 2330、0050），優先 Fugle 即時報價，fallback yfinance（約 15 分鐘延遲）
    - 美股：標準 ticker（如 AAPL、TSLA、MU、NVDA）
    - 韓股：6 位數字（如 005930）或別名 SMSN（三星）、SKH（SK 海力士）
    - 指數：^TWII、^GSPC、^IXIC、^DJI、^SOX
    - 中文別名：蘋果、特斯拉、輝達、微軟、谷歌、亞馬遜、超微、英特爾、美光、台積電、戴爾、三星、SK海力士 等

    加密化股票代幣（Tokenized Stocks）：
    下列標的在對應市場收盤後，回傳結果會自動附上 "token" 欄位，顯示對應加密代幣的即時價格：
      美股：AMZN/GOOGL/NVDA/INTC/TSLA/AMD/MSFT/SNDK/MU/TSM/DELL/SPY
      韓股：SMSN（三星）/SKH（SK海力士）
    開盤中只回傳正股，收盤後正股 + token 一起回傳。
    """
    yf_symbol, token_key, market = _resolve(symbol)

    # Fetch main stock price
    if market == "TW":
        clean = yf_symbol.replace(".TW", "").replace(".TWO", "")
        result = (_fetch_fugle(clean) if FUGLE_API_KEY else None) or _fetch_yf(yf_symbol)
    else:
        result = _fetch_yf(yf_symbol)

    result = result or {"error": f"無法取得 {symbol} 的股價"}

    # Attach tokenized stock price when market is closed
    if token_key and token_key in _STOCK_TOKEN_MAP:
        if _is_market_open(market):
            result["market_status"] = "open"
        else:
            token_symbol = _STOCK_TOKEN_MAP[token_key]
            token_data = _fetch_yf(token_symbol)
            if token_data:
                result["token"] = {
                    "symbol": token_symbol,
                    "price": token_data["price"],
                    "change_pct": token_data.get("change_pct"),
                    "source": token_data["source"],
                }
            result["market_status"] = "closed"

    return result


@mcp.tool()
def get_multiple_stock_prices(symbols: list[str]) -> dict:
    """
    批次查詢多檔股票報價，台股、美股、韓股可混合。
    範例：['2330', '0050', 'AAPL', 'TSLA', 'SMSN', 'MU']
    收盤時間的美股/韓股也會自動附上加密化代幣價格。
    """
    return {s: get_stock_price(s) for s in symbols}


@mcp.tool()
def get_technical_analysis(symbol: str, period: str = "3mo") -> dict:
    """
    取得技術分析指標。支援台股、美股、韓股，中文別名同 get_stock_price。

    參數：
    - symbol：股票代號或中文別名
    - period：歷史資料區間，預設 3mo（可選：1mo / 3mo / 6mo / 1y）

    回傳：
    - last_close：最新收盤價
    - rsi_14：相對強弱指標（>70 超買，<30 超賣）
    - macd / macd_signal / macd_hist：MACD 指標
    - ma_5 / ma_20 / ma_60：移動平均線
    - bb_upper / bb_mid / bb_lower：布林通道
    - volume：最新成交量
    - chart_url：（美股限定）Finviz 技術分析日線圖連結
    """
    yf_symbol, _, market = _resolve(symbol)

    try:
        df = yf.Ticker(yf_symbol).history(period=period)
        if df.empty:
            return {"error": f"無法取得 {symbol} 歷史資料"}

        close = df["Close"]

        # SMA
        ma5  = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]

        # RSI(14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi = (100 - 100 / (1 + rs)).iloc[-1]

        # MACD(12,26,9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist   = macd_line - signal_line

        # Bollinger Bands(20, 2σ)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20

        result = {
            "symbol": yf_symbol,
            "period": period,
            "last_close": _safe_float(close.iloc[-1]),
            "volume": int(df["Volume"].iloc[-1]) if not pd.isna(df["Volume"].iloc[-1]) else None,
            "rsi_14":      _safe_float(rsi),
            "macd":        _safe_float(macd_line.iloc[-1]),
            "macd_signal": _safe_float(signal_line.iloc[-1]),
            "macd_hist":   _safe_float(macd_hist.iloc[-1]),
            "ma_5":        _safe_float(ma5),
            "ma_20":       _safe_float(ma20),
            "ma_60":       _safe_float(ma60),
            "bb_upper":    _safe_float(bb_upper.iloc[-1]),
            "bb_mid":      _safe_float(sma20.iloc[-1]),
            "bb_lower":    _safe_float(bb_lower.iloc[-1]),
        }

        if market == "US" and not yf_symbol.startswith("^"):
            result["chart_url"] = f"https://finviz.com/chart.ashx?t={yf_symbol}&ty=c&ta=1&p=d"

        return result

    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def health(request):
        return JSONResponse({"status": "ok"})

    port = int(os.environ.get("PORT", 8000))
    app = Starlette(routes=[
        Route("/health", health),
        Mount("/", app=mcp.sse_app()),
    ])
    uvicorn.run(app, host="0.0.0.0", port=port)
