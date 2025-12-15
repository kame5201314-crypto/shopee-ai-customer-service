# 蝦皮 AI 自動客服系統

基於 Python Flask 的蝦皮商城 AI 自動客服系統，整合 Shopee Open Platform API V2 與 OpenAI GPT-4o。

## 功能特色

### 核心功能
- **OAuth 2.0 授權流程** - 安全的蝦皮帳號綁定
- **Token 自動刷新** - 每 3.5 小時自動更新，服務不中斷
- **Webhook 訊息監聽** - 即時接收客戶訊息
- **AI 智慧回覆** - 使用 GPT-4o 生成專業客服回覆
- **Webhook 簽章驗證** - 防止偽造請求

### 進階功能
- **對話上下文記憶** - AI 能記住對話歷史，提供更連貫的回覆
- **關鍵字自動回覆** - 常見問題秒回，節省 API 費用
- **多種訊息類型支援** - 處理文字、圖片、貼圖、訂單等訊息
- **速率限制防護** - 防止 API 被濫用
- **訊息記錄查詢** - 完整的對話歷史追蹤
- **Web 管理儀表板** - 視覺化監控與測試

## 系統需求

- Python 3.9+
- 蝦皮開發者帳號 (Partner ID & Key)
- OpenAI API Key
- ngrok (用於開發環境外部連線)

## 快速開始

### 1. 安裝套件

```bash
# 建立虛擬環境 (建議)
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# 安裝相依套件
pip install -r requirements.txt
```

### 2. 設定環境變數

```bash
# 複製環境變數範本
cp .env.example .env

# 編輯 .env 檔案，填入您的設定
```

必要設定項目：
| 變數 | 說明 |
|------|------|
| `SHOPEE_PARTNER_ID` | 蝦皮 Partner ID |
| `SHOPEE_PARTNER_KEY` | 蝦皮 Partner Key |
| `OPENAI_API_KEY` | OpenAI API 金鑰 |
| `REDIRECT_URL` | OAuth 回調網址 |

可選設定項目：
| 變數 | 預設值 | 說明 |
|------|--------|------|
| `APP_PORT` | 5000 | 伺服器埠號 |
| `SYSTEM_PROMPT` | (見範例) | AI 客服人設 |
| `MAX_CONVERSATION_HISTORY` | 10 | 保留對話歷史數量 |
| `RATE_LIMIT_PER_MINUTE` | 30 | 每分鐘最大請求數 |
| `ENABLE_KEYWORD_REPLY` | true | 是否啟用關鍵字回覆 |

### 3. 設定 ngrok (開發環境)

```bash
# 安裝 ngrok: https://ngrok.com/download

# 啟動 ngrok
ngrok http 5000
```

取得 ngrok URL 後：

1. 更新 `.env` 中的 `REDIRECT_URL`
2. 在蝦皮開發者後台設定：
   - **Redirect URL**: `https://xxx.ngrok.io/auth/callback`
   - **Webhook URL**: `https://xxx.ngrok.io/webhook`

### 4. 啟動伺服器

```bash
python app.py
```

### 5. 進行授權

1. 開啟瀏覽器訪問: `http://localhost:5000`
2. 點擊「開始授權」
3. 登入蝦皮帳號並同意授權
4. 授權成功後系統自動運作

## API 端點總覽

### 授權相關
| 端點 | 方法 | 說明 |
|------|------|------|
| `/auth/login` | GET | 開始 OAuth 授權流程 |
| `/auth/callback` | GET | OAuth 回調接收端點 |
| `/auth/refresh` | POST | 手動刷新 Token |

### Webhook
| 端點 | 方法 | 說明 |
|------|------|------|
| `/webhook` | POST | 蝦皮訊息 Webhook |

### 系統狀態
| 端點 | 方法 | 說明 |
|------|------|------|
| `/` | GET | 首頁 |
| `/status` | GET | 系統狀態 JSON |
| `/dashboard` | GET | 管理儀表板 |

### 管理 API
| 端點 | 方法 | 說明 |
|------|------|------|
| `/api/logs` | GET | 訊息記錄查詢 |
| `/api/conversations` | GET | 蝦皮聊天列表 |
| `/api/messages/<id>` | GET | 對話訊息詳情 |
| `/api/keyword-rules` | GET/POST | 關鍵字規則管理 |
| `/api/conversation-history/<user_id>` | GET/DELETE | 對話歷史管理 |

### 測試端點
| 端點 | 方法 | 說明 |
|------|------|------|
| `/test/webhook` | POST | 測試 AI 回覆 |
| `/test/send` | POST | 測試發送訊息 |

## 關鍵字規則

系統會自動建立 `keyword_rules.json`，包含預設規則：

```json
[
  {
    "keywords": ["運費", "運費多少", "免運"],
    "reply": "親愛的顧客您好！運費依據您的收件地址計算，滿 $499 即享免運優惠喔！",
    "enabled": true
  }
]
```

可透過 `/api/keyword-rules` API 或直接編輯檔案來管理規則。

## 檔案結構

```
shopee-ai-customer-service/
├── app.py                 # 主程式
├── .env                   # 環境變數 (需自行建立)
├── .env.example           # 環境變數範本
├── requirements.txt       # 套件清單
├── README.md              # 說明文件
├── tokens.json            # Token 儲存 (自動生成)
├── conversations.json     # 對話歷史 (自動生成)
├── messages_log.json      # 訊息記錄 (自動生成)
├── keyword_rules.json     # 關鍵字規則 (自動生成)
└── shopee_ai.log          # 系統日誌 (自動生成)
```

## 自訂 AI 人設

在 `.env` 中修改 `SYSTEM_PROMPT`：

```
SYSTEM_PROMPT=你是一位專業的3C產品客服，熟悉各種電子產品規格，請用專業但親切的語氣回覆客戶，回答不超過100字。
```

## 生產環境部署

### 使用 Gunicorn

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### 使用 Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
```

### 使用 systemd

```ini
[Unit]
Description=Shopee AI Customer Service
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/shopee-ai
ExecStart=/opt/shopee-ai/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

## 安全注意事項

- 請勿將 `.env` 檔案提交至版本控制
- 定期更換 API 金鑰
- 生產環境請使用 HTTPS
- 確保 Webhook 簽章驗證已啟用
- 設定適當的速率限制

## 故障排除

### Token 過期
系統會自動刷新 Token。若仍出現問題：
1. 檢查 `/status` 端點的 token_status
2. 嘗試手動刷新：`POST /auth/refresh`
3. 重新執行授權：訪問 `/auth/login`

### Webhook 無法接收
1. 確認 ngrok 正在運行
2. 確認蝦皮後台 Webhook URL 設定正確
3. 檢查 `shopee_ai.log` 日誌

### AI 回覆失敗
1. 確認 OpenAI API Key 有效
2. 確認帳戶餘額充足
3. 檢查日誌中的錯誤訊息

### 測試 AI 回覆
使用管理儀表板 `/dashboard` 或 API：

```bash
curl -X POST http://localhost:5000/test/webhook \
  -H "Content-Type: application/json" \
  -d '{"user_id": "12345", "message": "請問運費怎麼算？"}'
```

## 更新日誌

### v2.0.0
- 新增對話上下文記憶功能
- 新增關鍵字自動回覆
- 新增速率限制防護
- 新增 Web 管理儀表板
- 新增訊息記錄查詢
- 支援多種訊息類型
- 改進錯誤處理機制

### v1.0.0
- 初始版本
- OAuth 2.0 授權
- Token 自動刷新
- Webhook 訊息處理
- OpenAI GPT-4o 整合

## License

MIT License
