import os
import requests
import yfinance as yf
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stock-prices", host="0.0.0.0")

FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "")


def _is_tw_stock(symbol: str) -> bool:
    clean = symbol.upper().replace(".TW", "").replace(".TWO", "")
    return clean.isdigit() and len(clean) in (4, 5)


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


_INDEX_ALIASES: dict[str, str] = {
    "台灣加權": "^TWII", "加權指數": "^TWII", "台加": "^TWII", "TWII": "^TWII",
    "S&P500": "^GSPC", "SP500": "^GSPC",
    "NASDAQ": "^IXIC", "那斯達克": "^IXIC",
    "道瓊": "^DJI", "DOW": "^DJI",
    "費半": "^SOX", "SOX": "^SOX",
}


def _fetch_yf(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        is_index = symbol.startswith("^")
        if is_index:
            hist = ticker.history(period="5d", interval="1d")
        else:
            hist = ticker.history(period="1d", interval="1m")
            if hist.empty:
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


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """
    取得股票或指數即時報價。
    台股輸入代號（如 2330、0050），美股輸入 ticker（如 AAPL、TSLA）。
    指數：^TWII（台灣加權）、^GSPC（S&P500）、^IXIC（Nasdaq）、^DJI（道瓊）、^SOX（費半）。
    也接受中文別名：「台灣加權」、「那斯達克」、「道瓊」等。
    台股優先使用 Fugle 即時報價，無法取得時 fallback 到 yfinance（約 15 分鐘延遲）。
    """
    s = _INDEX_ALIASES.get(symbol.strip(), symbol).upper().strip()
    clean = s.replace(".TW", "").replace(".TWO", "")

    if _is_tw_stock(s):
        if FUGLE_API_KEY:
            result = _fetch_fugle(clean)
            if result:
                return result
        result = _fetch_yf(f"{clean}.TW")
        return result or {"error": f"無法取得 {symbol} 的股價"}

    result = _fetch_yf(s)
    return result or {"error": f"無法取得 {symbol} 的股價"}


@mcp.tool()
def get_multiple_stock_prices(symbols: list[str]) -> dict:
    """
    批次查詢多檔股票報價，台股美股可混合。
    範例：['2330', '0050', 'AAPL', 'TSLA']
    """
    return {s: get_stock_price(s) for s in symbols}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    app = mcp.sse_app()
    uvicorn.run(app, host="0.0.0.0", port=port)
