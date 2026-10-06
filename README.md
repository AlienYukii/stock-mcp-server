# Stock MCP Server

供 Claude 查詢即時股價與基本面的 MCP server。單檔 `server.py`,部署在 Render。

## 架構

```
Claude Desktop / Claude Code
        │  MCP over SSE
        ▼
Render (Starlette + Uvicorn)        ← GitHub push main 自動部署
  ├─ /health
  └─ / (FastMCP SSE app)  →  server.py 的 12 個 tools
        │
        ├─ 台股 ──→ Fugle API(即時)──fallback──→ yfinance (.TW)
        ├─ 美股 / 韓股 / 指數 ──→ yfinance ──fallback──→ Finviz snapshot
        ├─ 新聞 ──→ 美股 Finviz / 韓台 Google News RSS / 其他 yfinance
        └─ 收盤後 ──→ 對應加密化股票代幣價格 (yfinance)

快取層:TTL cache + stale-while-error(減輕 Yahoo 對 Render 共用 IP 的限流)
```

## 技術棧

| 類別 | 使用技術 |
|------|----------|
| 語言 | Python 3.13 |
| MCP | FastMCP (`mcp[cli]`),SSE 傳輸 |
| Web | Starlette、Uvicorn |
| 資料來源 | Fugle REST API、yfinance、Finviz(爬取)、Google News RSS |
| 解析 / 工具 | requests、beautifulsoup4、pytz |
| 部署 | Render(免費方案),GitHub 自動部署 |

## 功能(MCP Tools)

| Tool | 說明 |
|------|------|
| `get_stock_price` | 單一報價(台股 Fugle 優先),附市場開收盤狀態、資料時間戳 |
| `get_multiple_stock_prices` | 批次報價 |
| `get_technical_analysis` | RSI、MACD、MA、布林通道、成交量 |
| `get_fundamentals` | PE、EPS、市值、毛利/營益率、ROE/ROA 等 |
| `get_dividend_info` | 殖利率、配息、除息日、歷史股利 |
| `get_stock_news` | 依市場自動選新聞來源 |
| `get_analyst_ratings` | 分析師評級與目標價 |
| `get_market_overview` | 主要指數總覽 |
| `get_earnings_calendar` | 個股財報日 |
| `get_market_calendar` | FOMC 會議 + 指定個股財報日 |
| `compare_stocks` | 多檔股票比較 |
| `get_institutional_holders` | 機構持股 |

## 支援市場

| 市場 | 代號格式 | 範例 |
|------|----------|------|
| 台股 | 4–5 位數字 | `2330`、`0050` |
| 美股 | 標準 ticker | `AAPL`、`MU` |
| 韓股 | 6 位數字或別名 | `005930`、`SMSN`、`SKH` |
| 指數 | `^` 開頭 | `^TWII`、`^GSPC` |
| 中文別名 | 蘋果、輝達、台積電… | |

## 特色

- **多層 fallback**:Fugle → yfinance → Finviz,單一來源失效仍能回應
- **快取**:報價 60s、新聞/K 線 10min、基本面 15min、其餘 1h;過期且重抓失敗時回傳舊值
- **加密化股票代幣**:美股/韓股收盤後自動附對應代幣價格(如 NVDA → NVDAX-USD)
- **資料時間戳**:報價附 `price_time`、`fetched_at`,可判斷資料新舊

## 部署

- 環境變數:`FUGLE_API_KEY`(台股即時報價)、`PORT`(Render 自動注入)
- 安裝:`pip install -r requirements.txt`
- 啟動:`python server.py`
- 注意:`_FOMC_MEETINGS` 為硬編碼,每年需手動更新
