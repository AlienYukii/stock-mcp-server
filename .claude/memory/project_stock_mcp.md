---
name: project-stock-mcp
description: "Stock MCP server architecture, features, and deployment details"
metadata: 
  node_type: memory
  type: project
  originSessionId: ca2deb88-e2c9-46e0-8228-9e56dba0596b
---

## 專案概覽

**Stock MCP Server** — 供 Claude 查即時股價的 MCP server，單檔 `server.py`，部署在 Render。

**Repo**: `github.com:AlienYukii/stock-mcp-server.git`
**Render URL**: `https://stock-mcp-server-035f.onrender.com`
**Render Service ID**: `srv-d8ln1fnavr4c738p74qg`

---

## 技術棧

**語言 / 執行環境**
- **Python 3.13**

**框架 / 核心套件**

| 套件 | 用途 |
|------|------|
| `fastmcp` (`mcp[cli]`) | 建立 MCP Server，定義 tool（`@mcp.tool()` 裝飾器）|
| `starlette` | ASGI web framework，掛載路由 `/health` + MCP mount |
| `uvicorn` | ASGI server，實際跑 HTTP |
| `sse_starlette` | SSE 傳輸層（MCP over HTTP），FastMCP 內部使用 |

**資料來源**

| 套件 | 用途 |
|------|------|
| `requests` | 打 Fugle REST API 取台股即時報價 |
| `yfinance` | 美股 / 韓股 / 指數 / 加密代幣報價（15 分鐘延遲）|
| `pandas-ta` | 技術分析指標（RSI/MACD/BB/MA）|
| `pytz` | 市場交易時間判斷（US/KR）|

**MCP 協定**
- 傳輸層：**SSE（Server-Sent Events）**
- Claude Desktop / Claude Code 透過 SSE 連上 server

**資料流**
```
Claude → MCP (SSE) → server.py
                       ├─ 台股 → Fugle API (即時)
                       │         └─ fallback → yfinance (.TW)
                       ├─ 美股/韓股 → yfinance
                       └─ 收盤後有 token map → yfinance (加密代幣)
```

---

## 支援市場與代號規則

| 市場 | 格式 | 範例 |
|------|------|------|
| 台股 | 4-5 位數字 | 2330, 0050 |
| 美股 | 標準 ticker | AAPL, TSLA, MU |
| 韓股 | 6 位數字 或 別名 | 005930, SMSN, SKH |
| 指數 | ^ 開頭 | ^TWII, ^GSPC, ^IXIC |
| 中文別名 | 蘋果/特斯拉/輝達/美光/台積電/三星/SK海力士 等 | |

---

## MCP Tools

### `get_stock_price(symbol)`
- 台股優先 Fugle，fallback yfinance
- 若 symbol 在 `_STOCK_TOKEN_MAP` 且市場收盤 → 自動附 `token` 欄位（加密化股票代幣價格）
- 回傳 `market_status: "open" | "closed"`

### `get_multiple_stock_prices(symbols: list)`
- 批次查詢，內部逐一呼叫 `get_stock_price`

### `get_technical_analysis(symbol, period="3mo")`
- 回傳：RSI(14), MACD/Signal/Hist, MA5/20/60, Bollinger Bands, volume
- 美股額外附 Finviz 日線圖 URL：`finviz.com/chart.ashx?t={symbol}&ty=c&ta=1&p=d`
- 需要 `pandas-ta` 已安裝

---

## Tokenized Stock Map (`_STOCK_TOKEN_MAP`)

收盤後自動查對應加密代幣：

| 股票 | 代幣 |
|------|------|
| AMZN | AMZNX-USD |
| GOOGL | GOOGLX-USD |
| NVDA | NVDAX-USD |
| INTC | INTCX-USD |
| TSLA | TSLAX-USD |
| AMD | AMDX-USD |
| MSFT | MSFTX-USD |
| SNDK | SNDK-USD |
| MU | MUON-USD |
| TSM | TSMON-USD |
| DELL | DELLON-USD |
| SPY | SPYON-USD |
| SKH (SK Hynix) | 000660-USD |
| SMSN (Samsung) | 005930-USD |

---

## 部署

- **平台**: Render 免費方案（有冷啟動問題，已用 ping 保持活躍）
- **Auto-deploy**: 接 GitHub，push main 自動觸發
- **安裝套件**: Render 每次 deploy 自動跑 `pip install -r requirements.txt`
- **環境變數**: `FUGLE_API_KEY`（台股即時報價）、`PORT`（Render 自動注入）

**Why:** 供 Claude 查即時股價，包含台股/美股/韓股/加密化代幣，所有行為封裝在 tool docstring，Claude 不需額外 system prompt 說明。
