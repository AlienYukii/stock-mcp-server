import os
import requests
import yfinance as yf
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stock-prices")

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


def _fetch_yf(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m")
        if hist.empty:
            hist = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        info = ticker.fast_info
        currency = getattr(info, "currency", "USD") or "USD"
        prev = getattr(info, "previous_close", None)
        change_pct = round((price - prev) / prev * 100, 2) if prev else None
        return {"symbol": symbol, "price": price, "change_pct": change_pct, "currency": currency, "source": "yfinance"}
    except Exception:
        return None


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """
    取得股票即時報價。
    台股輸入代號（如 2330、0050），美股輸入 ticker（如 AAPL、TSLA）。
    台股優先使用 Fugle 即時報價，無法取得時 fallback 到 yfinance（約 15 分鐘延遲）。
    """
    s = symbol.upper().strip()
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
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
