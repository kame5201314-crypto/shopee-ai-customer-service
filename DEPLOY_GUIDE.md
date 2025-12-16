# 蝦皮 AI 自動客服系統 - 部署指南

## 方法一：使用 Render 部署（推薦）

### 步驟 1：創建 GitHub 倉庫

1. 開啟瀏覽器，前往 https://github.com/new
2. 輸入倉庫名稱：`shopee-ai-customer-service`
3. 選擇 Public（公開）
4. 點擊 "Create repository"

### 步驟 2：推送程式碼

在終端機中執行：

```bash
cd "C:\Users\kawei\Desktop\shopee-ai-customer-service"
git remote add origin https://github.com/你的使用者名稱/shopee-ai-customer-service.git
git branch -M main
git push -u origin main
```

### 步驟 3：部署到 Render

1. 前往 https://render.com 並註冊/登入
2. 點擊 "New +" → "Web Service"
3. 連接你的 GitHub 帳號
4. 選擇 `shopee-ai-customer-service` 倉庫
5. 設定：
   - Name: `shopee-ai-dashboard`
   - Environment: `Python`
   - Build Command: `pip install -r requirements-cloud.txt`
   - Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
6. 點擊 "Create Web Service"
7. 等待部署完成後，你會得到一個類似 `https://shopee-ai-dashboard.onrender.com` 的網址

## 方法二：使用 Railway 部署

1. 前往 https://railway.app
2. 登入 GitHub 帳號
3. 點擊 "New Project" → "Deploy from GitHub repo"
4. 選擇 `shopee-ai-customer-service` 倉庫
5. Railway 會自動偵測並部署

## 方法三：使用 Vercel 部署

1. 前往 https://vercel.com
2. 導入 GitHub 倉庫
3. 部署即可

## 專案結構說明

```
shopee-ai-customer-service/
├── server.py           # 雲端控制台（FastAPI）
├── main.py             # 本地機器人（Playwright）
├── render.yaml         # Render 部署設定
├── requirements.txt    # 完整套件（含 Playwright）
├── requirements-cloud.txt  # 雲端套件（不含 Playwright）
├── knowledge_base.txt  # 知識庫
├── .env.example        # 環境變數範例
└── .gitignore          # Git 忽略檔案
```

## 重要說明

- 雲端控制台（server.py）用於設定和下載配置檔
- 實際的自動回覆機器人（main.py）需要在本地電腦執行
- 本地執行步驟：
  1. `pip install -r requirements.txt`
  2. `playwright install chromium`
  3. 複製 .env.example 為 .env 並填入 OpenAI API Key
  4. `python main.py`
