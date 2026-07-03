import os
import xml.etree.ElementTree as ET
from datetime import time as dtime
from datetime import datetime

import pandas as pd
import pytz
import requests
import yfinance as yf
from bs4 import BeautifulSoup
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

# ── KR ticker code → Korean company name (for Google News queries) ───────────
_KR_NAME_MAP: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대자동차",
    "000270": "기아",
    "035420": "네이버",
    "035720": "카카오",
    "051910": "LG화학",
    "005490": "포스코홀딩스",
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


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _fetch_finviz_news(symbol: str, limit: int) -> list[dict] | None:
    """Scrape the news table from a Finviz quote page. US stocks only."""
    url = f"https://finviz.com/quote.ashx?t={symbol}&p=d"
    try:
        resp = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", class_="fullview-news-outer")
        if table is None:
            return None
        items = []
        last_date = None
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            a = row.find("a")
            if len(cells) < 2 or a is None:
                continue
            # First cell: "Jul-02-26 08:30AM" on the first item of a day,
            # then time-only ("07:15AM") for the rest of that day.
            ts = cells[0].get_text(strip=True)
            if " " in ts:
                last_date, ts_time = ts.rsplit(" ", 1)
            else:
                ts_time = ts
            span = row.find("span")
            publisher = span.get_text(strip=True).strip("()") if span else None
            items.append({
                "title":     a.get_text(strip=True),
                "publisher": publisher,
                "link":      a.get("href"),
                "published": f"{last_date} {ts_time}" if last_date else ts_time,
            })
            if len(items) >= limit:
                break
        return items or None
    except Exception:
        return None


def _fetch_google_news(query: str, hl: str, gl: str, ceid: str, limit: int) -> list[dict] | None:
    """Fetch news items from Google News RSS search."""
    try:
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": hl, "gl": gl, "ceid": ceid},
            headers={"User-Agent": _BROWSER_UA},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            pub = item.findtext("pubDate")
            try:
                pub = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            items.append({
                "title":     item.findtext("title"),
                "publisher": item.findtext("source"),
                "link":      item.findtext("link"),
                "published": pub,
            })
            if len(items) >= limit:
                break
        return items or None
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


@mcp.tool()
def get_fundamentals(symbol: str) -> dict:
    """
    取得股票基本面資料。支援美股、台股、韓股，中文別名同 get_stock_price。

    回傳：
    - pe_trailing / pe_forward：本益比（歷史 / 預估）
    - eps_ttm：每股盈餘（近四季）
    - market_cap：市值
    - revenue_ttm：年度營收
    - gross_margin / operating_margin：毛利率 / 營業利益率
    - roe / roa：股東權益報酬率 / 資產報酬率
    - debt_to_equity：負債權益比
    - current_ratio：流動比率
    - 52w_high / 52w_low：52 週高低點
    """
    yf_symbol, _, _ = _resolve(symbol)
    try:
        info = yf.Ticker(yf_symbol).info
        return {
            "symbol":           yf_symbol,
            "pe_trailing":      _safe_float(info.get("trailingPE")),
            "pe_forward":       _safe_float(info.get("forwardPE")),
            "eps_ttm":          _safe_float(info.get("epsTrailingTwelveMonths")),
            "market_cap":       info.get("marketCap"),
            "revenue_ttm":      info.get("totalRevenue"),
            "gross_margin":     _safe_float(info.get("grossMargins")),
            "operating_margin": _safe_float(info.get("operatingMargins")),
            "roe":              _safe_float(info.get("returnOnEquity")),
            "roa":              _safe_float(info.get("returnOnAssets")),
            "debt_to_equity":   _safe_float(info.get("debtToEquity")),
            "current_ratio":    _safe_float(info.get("currentRatio")),
            "52w_high":         _safe_float(info.get("fiftyTwoWeekHigh")),
            "52w_low":          _safe_float(info.get("fiftyTwoWeekLow")),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_dividend_info(symbol: str) -> dict:
    """
    取得股息與配息資訊。支援美股、台股、韓股，中文別名同 get_stock_price。

    回傳：
    - dividend_yield：殖利率（小數，如 0.015 = 1.5%）
    - annual_dividend：年度配息金額（每股）
    - payout_ratio：配息率
    - ex_dividend_date：除息日
    - last_dividends：近 8 筆配息紀錄（日期 + 金額）
    """
    yf_symbol, _, _ = _resolve(symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.info
        dividends = ticker.dividends

        last_divs = []
        if not dividends.empty:
            for dt, val in dividends.tail(8).items():
                last_divs.append({"date": dt.strftime("%Y-%m-%d"), "amount": round(float(val), 4)})

        ex_date = info.get("exDividendDate")
        if isinstance(ex_date, (int, float)) and ex_date:
            try:
                ex_date = datetime.utcfromtimestamp(ex_date).strftime("%Y-%m-%d")
            except Exception:
                ex_date = str(ex_date)

        return {
            "symbol":           yf_symbol,
            "dividend_yield":   _safe_float(info.get("dividendYield")),
            "annual_dividend":  _safe_float(info.get("dividendRate")),
            "payout_ratio":     _safe_float(info.get("payoutRatio")),
            "ex_dividend_date": ex_date,
            "last_dividends":   last_divs,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_stock_news(symbol: str, limit: int = 8) -> dict:
    """
    取得股票最新新聞標題。支援美股、台股、韓股，中文別名同 get_stock_price。

    新聞來源依市場自動選擇：
    - 美股：Finviz（即時聚合，品質最佳），失敗時 fallback yfinance
    - 韓股：Google News 韓文搜尋（三星/SK海力士等大型股用韓文公司名查詢）
    - 台股：Google News 繁中搜尋
    - 指數或其他：yfinance

    參數：
    - symbol：股票代號或中文別名
    - limit：回傳新聞數量（預設 8，最多 20）

    回傳：
    - source：實際使用的新聞來源
    - news：新聞列表，每筆含 title、publisher、link、published
      （韓股新聞標題為韓文、台股為中文，可自行翻譯摘要給使用者）
    """
    yf_symbol, _, market = _resolve(symbol)
    limit = min(int(limit), 20)

    if market == "US":
        items = _fetch_finviz_news(yf_symbol, limit)
        if items:
            return {"symbol": yf_symbol, "source": "Finviz", "count": len(items), "news": items}

    elif market == "KR":
        code = yf_symbol.split(".")[0]
        query = _KR_NAME_MAP.get(code, f"{code} 주식")
        items = _fetch_google_news(query, hl="ko", gl="KR", ceid="KR:ko", limit=limit)
        if items:
            return {"symbol": yf_symbol, "source": f"Google News ({query})", "count": len(items), "news": items}

    elif market == "TW":
        code = yf_symbol.split(".")[0]
        items = _fetch_google_news(f"{code} 股價", hl="zh-TW", gl="TW", ceid="TW:zh-Hant", limit=limit)
        if items:
            return {"symbol": yf_symbol, "source": "Google News", "count": len(items), "news": items}

    # Fallback: yfinance
    try:
        news_raw = yf.Ticker(yf_symbol).news or []
        items = []
        for n in news_raw[:limit]:
            # yfinance >=0.2.37 wraps content under a "content" key
            c = n.get("content", n)
            pub_time = c.get("pubDate") or c.get("providerPublishTime")
            if isinstance(pub_time, (int, float)):
                pub_time = datetime.utcfromtimestamp(pub_time).strftime("%Y-%m-%d %H:%M")
            canonical = c.get("canonicalUrl")
            link = (canonical.get("url") if isinstance(canonical, dict) else canonical) or c.get("link") or n.get("link")
            provider = c.get("provider")
            publisher = (provider.get("displayName") if isinstance(provider, dict) else provider) or c.get("publisher") or n.get("publisher")
            items.append({
                "title":     c.get("title") or n.get("title"),
                "publisher": publisher,
                "link":      link,
                "published": pub_time,
            })
        return {"symbol": yf_symbol, "source": "yfinance", "count": len(items), "news": items}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_analyst_ratings(symbol: str) -> dict:
    """
    取得分析師評級與目標價。主要適用美股。

    回傳：
    - recommendation：綜合評級（如 buy / hold / sell）
    - target_price / target_high / target_low：目標價（平均 / 最高 / 最低）
    - num_analysts：覆蓋分析師人數
    - ratings_breakdown：最新一期 Strong Buy / Buy / Hold / Sell / Strong Sell 人數分佈
    """
    yf_symbol, _, _ = _resolve(symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.info
        result = {
            "symbol":         yf_symbol,
            "recommendation": info.get("recommendationKey"),
            "target_price":   _safe_float(info.get("targetMeanPrice")),
            "target_high":    _safe_float(info.get("targetHighPrice")),
            "target_low":     _safe_float(info.get("targetLowPrice")),
            "num_analysts":   info.get("numberOfAnalystOpinions"),
        }
        rec = ticker.recommendations
        if rec is not None and not rec.empty:
            latest = rec.iloc[-1]
            result["ratings_breakdown"] = {
                "strong_buy":  int(latest.get("strongBuy",  0)),
                "buy":         int(latest.get("buy",         0)),
                "hold":        int(latest.get("hold",        0)),
                "sell":        int(latest.get("sell",        0)),
                "strong_sell": int(latest.get("strongSell", 0)),
            }
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_market_overview() -> dict:
    """
    一次取得全球主要市場大盤快照，不需要傳入任何參數。

    回傳指數：
    - 台灣加權（^TWII）
    - S&P 500（^GSPC）
    - NASDAQ（^IXIC）
    - 道瓊（^DJI）
    - 費城半導體（^SOX）
    - VIX 恐慌指數（^VIX）

    每個指數回傳：price、change_pct、currency
    """
    _INDICES = {
        "台灣加權 TWII": "^TWII",
        "S&P 500":       "^GSPC",
        "NASDAQ":        "^IXIC",
        "道瓊 DJI":      "^DJI",
        "費半 SOX":      "^SOX",
        "VIX":           "^VIX",
    }
    result = {}
    for name, sym in _INDICES.items():
        data = _fetch_yf(sym)
        if data:
            result[name] = {
                "symbol":     sym,
                "price":      data["price"],
                "change_pct": data.get("change_pct"),
                "currency":   data.get("currency", "USD"),
            }
        else:
            result[name] = {"symbol": sym, "error": "無法取得"}
    return result


@mcp.tool()
def get_earnings_calendar(symbol: str) -> dict:
    """
    查詢股票下一次財報發布日及相關預估值。主要適用美股。
    中文別名同 get_stock_price（如「蘋果」、「輝達」）。

    回傳：
    - earnings_date：財報發布日（可能含區間）
    - eps_estimate：EPS 預估值
    - revenue_estimate：營收預估值
    - last_earnings_date / last_eps_actual：上一季財報日與實際 EPS
    """
    yf_symbol, _, _ = _resolve(symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        cal = ticker.calendar

        def _fmt_date(val):
            if val is None:
                return None
            if hasattr(val, "strftime"):
                return val.strftime("%Y-%m-%d")
            return str(val)

        if cal is None or (hasattr(cal, "empty") and cal.empty):
            return {"symbol": yf_symbol, "error": "無財報行事曆資料"}

        # yfinance returns a dict
        if isinstance(cal, dict):
            earnings_dates = cal.get("Earnings Date", [])
            if hasattr(earnings_dates, "tolist"):
                earnings_dates = earnings_dates.tolist()
            dates = [_fmt_date(d) for d in (earnings_dates if isinstance(earnings_dates, list) else [earnings_dates])]
            return {
                "symbol":           yf_symbol,
                "earnings_date":    dates,
                "eps_estimate":     _safe_float(cal.get("EPS Estimate")),
                "revenue_estimate": cal.get("Revenue Estimate"),
            }

        # fallback: DataFrame
        return {"symbol": yf_symbol, "calendar_raw": str(cal)}

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def compare_stocks(symbols: list[str], period: str = "1y") -> dict:
    """
    比較多檔股票在相同期間的報酬率，並由高到低排名。
    台股、美股、韓股可混合，中文別名同 get_stock_price。

    參數：
    - symbols：股票代號或中文別名清單，如 ['AAPL', 'NVDA', '2330', 'TSLA']
    - period：比較區間，預設 1y（可選：1mo / 3mo / 6mo / 1y / 2y / 5y）

    回傳：
    - ranked：依報酬率排序的清單，每筆含 symbol、start_price、end_price、return_pct
    - period：使用的區間
    """
    results = []
    for raw in symbols:
        yf_symbol, _, _ = _resolve(raw)
        try:
            hist = yf.Ticker(yf_symbol).history(period=period)
            if hist.empty or len(hist) < 2:
                results.append({"symbol": yf_symbol, "input": raw, "error": "資料不足"})
                continue
            start = float(hist["Close"].iloc[0])
            end   = float(hist["Close"].iloc[-1])
            ret   = round((end - start) / start * 100, 2)
            results.append({
                "symbol":      yf_symbol,
                "input":       raw,
                "start_price": round(start, 4),
                "end_price":   round(end, 4),
                "return_pct":  ret,
            })
        except Exception as e:
            results.append({"symbol": yf_symbol, "input": raw, "error": str(e)})

    ranked = sorted(
        [r for r in results if "return_pct" in r],
        key=lambda x: x["return_pct"],
        reverse=True,
    )
    errors = [r for r in results if "error" in r]
    return {"period": period, "ranked": ranked, "errors": errors}


@mcp.tool()
def get_institutional_holders(symbol: str) -> dict:
    """
    取得機構法人持股資訊。主要適用美股。
    中文別名同 get_stock_price（如「蘋果」、「輝達」）。

    回傳：
    - top_holders：前 10 大機構持股人（機構名、持股數、佔比、市值）
    - major_holders：大股東結構摘要（如散戶比例、機構持股比例）
    """
    yf_symbol, _, _ = _resolve(symbol)
    try:
        ticker = yf.Ticker(yf_symbol)

        inst = ticker.institutional_holders
        major = ticker.major_holders

        top = []
        if inst is not None and not inst.empty:
            for _, row in inst.head(10).iterrows():
                entry = {}
                for col in inst.columns:
                    val = row[col]
                    if hasattr(val, "strftime"):
                        val = val.strftime("%Y-%m-%d")
                    elif hasattr(val, "item"):
                        val = val.item()
                    entry[col] = val
                top.append(entry)

        major_dict = {}
        if major is not None and not major.empty:
            for _, row in major.iterrows():
                key = str(row.iloc[1]) if len(row) > 1 else str(_)
                val = str(row.iloc[0])
                major_dict[key] = val

        return {
            "symbol":        yf_symbol,
            "top_holders":   top,
            "major_holders": major_dict,
        }
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
