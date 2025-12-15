#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服系統 - 雲端控制台
可部署到 Render / Railway / Vercel
"""

import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

# ============================================
# 設定儲存 (記憶體 + 檔案)
# ============================================

CONFIG_FILE = "config_data.json"

# 預設設定
DEFAULT_CONFIG = {
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "shopee_chat_url": "https://seller.shopee.tw/portal/chatroom",
    "refresh_min": 30,
    "refresh_max": 60,
    "typing_min": 0.1,
    "typing_max": 0.3,
    "send_wait_min": 1.0,
    "send_wait_max": 3.0,
    "auto_reply": True,
    "typo_simulation": True,
    "use_knowledge_base": True,
    "system_prompt": "你是一位親切專業的電商客服人員。請用繁體中文回覆客戶問題。回答要簡潔有禮貌，不超過100字。",
    "knowledge_base": """【商店資訊】
商店名稱：我的蝦皮商店
營業時間：週一至週五 9:00-18:00

【運費】
滿 $499 免運
一般運費 $60

【退換貨】
7天鑑賞期
商品需保持完整

【常見問題】
Q: 什麼時候出貨？
A: 訂單確認後 1-2 個工作天內出貨
"""
}

# 記憶體中的設定
current_config = DEFAULT_CONFIG.copy()

def load_config():
    """載入設定"""
    global current_config
    if Path(CONFIG_FILE).exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                current_config.update(saved)
        except:
            pass
    return current_config

def save_config(config: dict):
    """儲存設定"""
    global current_config
    current_config.update(config)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(current_config, f, ensure_ascii=False, indent=2)
    except:
        pass  # 雲端可能無法寫檔，忽略錯誤

# 啟動時載入
load_config()

# ============================================
# FastAPI
# ============================================

app = FastAPI(title="蝦皮 AI 客服控制台")


class ConfigModel(BaseModel):
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    shopee_chat_url: str = ""
    refresh_min: int = 30
    refresh_max: int = 60
    typing_min: float = 0.1
    typing_max: float = 0.3
    send_wait_min: float = 1.0
    send_wait_max: float = 3.0
    auto_reply: bool = True
    typo_simulation: bool = True
    use_knowledge_base: bool = True
    system_prompt: str = ""
    knowledge_base: str = ""


@app.get("/", response_class=HTMLResponse)
async def index():
    return DASHBOARD_HTML


@app.get("/api/config")
async def get_config():
    config = current_config.copy()
    # 遮蔽 API Key
    if config.get("openai_api_key"):
        key = config["openai_api_key"]
        config["api_key_display"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "已設定"
    else:
        config["api_key_display"] = "未設定"
    return config


@app.post("/api/config")
async def update_config(config: ConfigModel):
    data = config.model_dump()
    # 如果 API Key 為空，保留舊的
    if not data.get("openai_api_key"):
        data["openai_api_key"] = current_config.get("openai_api_key", "")
    save_config(data)
    return {"success": True, "message": "設定已儲存"}


@app.get("/api/download-env")
async def download_env():
    """下載 .env 設定檔"""
    env_content = f"""# 蝦皮 AI 客服系統設定檔
# 請將此檔案放到專案目錄並命名為 .env

# OpenAI API Key
OPENAI_API_KEY={current_config.get('openai_api_key', '')}

# AI 模型
OPENAI_MODEL={current_config.get('openai_model', 'gpt-4o-mini')}

# 蝦皮聊天頁面網址
SHOPEE_CHAT_URL={current_config.get('shopee_chat_url', 'https://seller.shopee.tw/portal/chatroom')}

# 刷新間隔 (秒)
REFRESH_MIN_SECONDS={current_config.get('refresh_min', 30)}
REFRESH_MAX_SECONDS={current_config.get('refresh_max', 60)}

# 打字速度 (秒/字)
TYPING_MIN_DELAY={current_config.get('typing_min', 0.1)}
TYPING_MAX_DELAY={current_config.get('typing_max', 0.3)}

# 發送前等待 (秒)
SEND_WAIT_MIN={current_config.get('send_wait_min', 1.0)}
SEND_WAIT_MAX={current_config.get('send_wait_max', 3.0)}

# AI 系統提示詞
SYSTEM_PROMPT={current_config.get('system_prompt', '')}
"""
    return Response(
        content=env_content,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=.env"}
    )


@app.get("/api/download-knowledge")
async def download_knowledge():
    """下載知識庫"""
    content = current_config.get("knowledge_base", "")
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=knowledge_base.txt"}
    )


# ============================================
# HTML 模板
# ============================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>蝦皮 AI 客服控制台</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        .gradient-bg { background: linear-gradient(135deg, #ee4d2d 0%, #ff6b4a 100%); }
        .card { background: white; border-radius: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); }
        .btn { transition: all 0.3s; cursor: pointer; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.15); }
        .btn-primary { background: linear-gradient(135deg, #ee4d2d 0%, #ff6b4a 100%); }
        .btn-green { background: linear-gradient(135deg, #10b981 0%, #34d399 100%); }
        .btn-blue { background: linear-gradient(135deg, #3b82f6 0%, #60a5fa 100%); }
        .toggle { width: 56px; height: 28px; background: #d1d5db; border-radius: 14px; position: relative; cursor: pointer; transition: 0.3s; }
        .toggle.active { background: #ee4d2d; }
        .toggle::after { content: ''; position: absolute; width: 22px; height: 22px; background: white; border-radius: 50%; top: 3px; left: 3px; transition: 0.3s; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
        .toggle.active::after { left: 31px; }
        .input-field { border: 2px solid #e5e7eb; border-radius: 12px; padding: 14px 18px; width: 100%; transition: 0.3s; font-size: 15px; }
        .input-field:focus { border-color: #ee4d2d; outline: none; box-shadow: 0 0 0 4px rgba(238,77,45,0.1); }
        .tab { padding: 16px 28px; cursor: pointer; border-bottom: 3px solid transparent; transition: 0.3s; font-weight: 500; }
        .tab:hover { background: #fef2f2; }
        .tab.active { border-color: #ee4d2d; color: #ee4d2d; background: #fef2f2; }
        .section-title { font-size: 18px; font-weight: 700; color: #1f2937; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
        .help-text { font-size: 13px; color: #9ca3af; margin-top: 6px; }
    </style>
</head>
<body class="bg-gray-50 min-h-screen">
    <!-- Header -->
    <header class="gradient-bg text-white shadow-xl">
        <div class="max-w-5xl mx-auto px-6 py-8">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-5">
                    <div class="w-16 h-16 bg-white/20 rounded-2xl flex items-center justify-center backdrop-blur">
                        <i class="fas fa-robot text-3xl"></i>
                    </div>
                    <div>
                        <h1 class="text-3xl font-bold">蝦皮 AI 客服控制台</h1>
                        <p class="text-white/80 mt-1">Shopee AI Customer Service Dashboard</p>
                    </div>
                </div>
            </div>
        </div>
    </header>

    <main class="max-w-5xl mx-auto px-6 py-8">
        <!-- 說明卡片 -->
        <div class="card p-6 mb-8 border-l-4 border-orange-500">
            <div class="flex items-start gap-4">
                <div class="w-12 h-12 bg-orange-100 rounded-xl flex items-center justify-center flex-shrink-0">
                    <i class="fas fa-info-circle text-orange-500 text-xl"></i>
                </div>
                <div>
                    <h3 class="font-bold text-gray-800 text-lg">使用說明</h3>
                    <p class="text-gray-600 mt-2">
                        1. 在此頁面設定好所有參數<br>
                        2. 點擊「下載設定檔」取得 <code class="bg-gray-100 px-2 py-1 rounded">.env</code> 檔案<br>
                        3. 將設定檔放到你電腦的專案目錄中<br>
                        4. 在電腦執行 <code class="bg-gray-100 px-2 py-1 rounded">python main.py</code> 啟動機器人
                    </p>
                </div>
            </div>
        </div>

        <!-- 下載按鈕區 -->
        <div class="card p-6 mb-8">
            <div class="flex flex-wrap gap-4 justify-center">
                <button onclick="downloadEnv()" class="btn btn-green text-white px-8 py-4 rounded-xl font-bold text-lg flex items-center gap-3">
                    <i class="fas fa-download"></i> 下載設定檔 (.env)
                </button>
                <button onclick="downloadKnowledge()" class="btn btn-blue text-white px-8 py-4 rounded-xl font-bold text-lg flex items-center gap-3">
                    <i class="fas fa-book"></i> 下載知識庫
                </button>
            </div>
        </div>

        <!-- 設定區域 -->
        <div class="card overflow-hidden">
            <!-- 標籤頁 -->
            <div class="flex border-b bg-gray-50 overflow-x-auto">
                <div class="tab active" onclick="showTab('basic')"><i class="fas fa-cog mr-2"></i>基本設定</div>
                <div class="tab" onclick="showTab('timing')"><i class="fas fa-clock mr-2"></i>時間設定</div>
                <div class="tab" onclick="showTab('switches')"><i class="fas fa-toggle-on mr-2"></i>功能開關</div>
                <div class="tab" onclick="showTab('prompt')"><i class="fas fa-comment mr-2"></i>AI 提示詞</div>
                <div class="tab" onclick="showTab('knowledge')"><i class="fas fa-book mr-2"></i>知識庫</div>
            </div>

            <!-- 基本設定 -->
            <div id="panel-basic" class="p-8">
                <div class="section-title">
                    <i class="fas fa-key text-purple-500"></i> OpenAI API 設定
                </div>

                <div class="space-y-6 max-w-2xl">
                    <div>
                        <label class="block font-medium text-gray-700 mb-2">API Key</label>
                        <input type="password" id="cfg-api-key" class="input-field" placeholder="sk-...">
                        <p class="help-text">目前狀態: <span id="api-key-status" class="font-medium">檢查中...</span></p>
                        <p class="help-text">取得 API Key: <a href="https://platform.openai.com/api-keys" target="_blank" class="text-blue-500 hover:underline">platform.openai.com/api-keys</a></p>
                    </div>

                    <div>
                        <label class="block font-medium text-gray-700 mb-2">AI 模型</label>
                        <select id="cfg-model" class="input-field">
                            <option value="gpt-4o-mini">GPT-4o Mini (推薦，便宜快速)</option>
                            <option value="gpt-4o">GPT-4o (更強，較貴)</option>
                            <option value="gpt-4-turbo">GPT-4 Turbo</option>
                            <option value="gpt-3.5-turbo">GPT-3.5 Turbo (最便宜)</option>
                        </select>
                    </div>

                    <div>
                        <label class="block font-medium text-gray-700 mb-2">蝦皮聊天頁面網址</label>
                        <input type="url" id="cfg-url" class="input-field" placeholder="https://seller.shopee.tw/portal/chatroom">
                        <p class="help-text">台灣蝦皮賣家中心聊天頁面</p>
                    </div>
                </div>

                <button onclick="saveConfig()" class="btn btn-primary text-white px-8 py-4 rounded-xl font-bold mt-8">
                    <i class="fas fa-save mr-2"></i> 儲存設定
                </button>
            </div>

            <!-- 時間設定 -->
            <div id="panel-timing" class="p-8 hidden">
                <div class="section-title">
                    <i class="fas fa-clock text-blue-500"></i> 時間參數設定
                </div>

                <div class="space-y-8 max-w-2xl">
                    <div>
                        <label class="block font-medium text-gray-700 mb-3">刷新間隔 (秒)</label>
                        <div class="flex items-center gap-4">
                            <input type="number" id="cfg-refresh-min" class="input-field w-32" value="30">
                            <span class="text-gray-400 text-xl">~</span>
                            <input type="number" id="cfg-refresh-max" class="input-field w-32" value="60">
                            <span class="text-gray-500">秒</span>
                        </div>
                        <p class="help-text">每隔這段時間檢查一次新訊息 (建議 30-60 秒)</p>
                    </div>

                    <div>
                        <label class="block font-medium text-gray-700 mb-3">打字速度 (秒/字)</label>
                        <div class="flex items-center gap-4">
                            <input type="number" step="0.05" id="cfg-typing-min" class="input-field w-32" value="0.1">
                            <span class="text-gray-400 text-xl">~</span>
                            <input type="number" step="0.05" id="cfg-typing-max" class="input-field w-32" value="0.3">
                            <span class="text-gray-500">秒</span>
                        </div>
                        <p class="help-text">每個字元輸入的間隔，模擬真人打字 (防封號關鍵)</p>
                    </div>

                    <div>
                        <label class="block font-medium text-gray-700 mb-3">發送前等待 (秒)</label>
                        <div class="flex items-center gap-4">
                            <input type="number" step="0.5" id="cfg-send-min" class="input-field w-32" value="1.0">
                            <span class="text-gray-400 text-xl">~</span>
                            <input type="number" step="0.5" id="cfg-send-max" class="input-field w-32" value="3.0">
                            <span class="text-gray-500">秒</span>
                        </div>
                        <p class="help-text">打完字後等待一段時間再發送，模擬真人檢查訊息</p>
                    </div>
                </div>

                <button onclick="saveConfig()" class="btn btn-primary text-white px-8 py-4 rounded-xl font-bold mt-8">
                    <i class="fas fa-save mr-2"></i> 儲存設定
                </button>
            </div>

            <!-- 功能開關 -->
            <div id="panel-switches" class="p-8 hidden">
                <div class="section-title">
                    <i class="fas fa-toggle-on text-green-500"></i> 功能開關
                </div>

                <div class="space-y-6 max-w-2xl">
                    <div class="flex items-center justify-between p-6 bg-gray-50 rounded-2xl">
                        <div>
                            <h4 class="font-bold text-gray-800 text-lg">自動回覆</h4>
                            <p class="text-gray-500 mt-1">開啟 AI 自動回覆客戶訊息</p>
                        </div>
                        <div class="toggle active" id="toggle-auto-reply" onclick="toggleSwitch('auto_reply')"></div>
                    </div>

                    <div class="flex items-center justify-between p-6 bg-gray-50 rounded-2xl">
                        <div>
                            <h4 class="font-bold text-gray-800 text-lg">打字錯誤模擬</h4>
                            <p class="text-gray-500 mt-1">偶爾打錯字再刪除，讓行為更像真人</p>
                        </div>
                        <div class="toggle active" id="toggle-typo" onclick="toggleSwitch('typo_simulation')"></div>
                    </div>

                    <div class="flex items-center justify-between p-6 bg-gray-50 rounded-2xl">
                        <div>
                            <h4 class="font-bold text-gray-800 text-lg">參考知識庫</h4>
                            <p class="text-gray-500 mt-1">AI 回覆時參考知識庫中的商店資訊</p>
                        </div>
                        <div class="toggle active" id="toggle-kb" onclick="toggleSwitch('use_knowledge_base')"></div>
                    </div>
                </div>
            </div>

            <!-- AI 提示詞 -->
            <div id="panel-prompt" class="p-8 hidden">
                <div class="section-title">
                    <i class="fas fa-comment-dots text-pink-500"></i> AI 系統提示詞
                </div>

                <p class="text-gray-600 mb-4">設定 AI 的角色和回覆風格，這會影響 AI 的回答方式。</p>

                <textarea id="cfg-prompt" rows="10" class="input-field font-mono" placeholder="輸入 AI 系統提示詞..."></textarea>

                <div class="mt-4 p-4 bg-blue-50 rounded-xl">
                    <p class="text-blue-800 text-sm">
                        <i class="fas fa-lightbulb mr-2"></i>
                        <strong>提示:</strong> 可以包含回覆原則、語氣要求、字數限制等。例如「回答不超過 100 字」「使用友善語氣」等。
                    </p>
                </div>

                <button onclick="saveConfig()" class="btn btn-primary text-white px-8 py-4 rounded-xl font-bold mt-8">
                    <i class="fas fa-save mr-2"></i> 儲存設定
                </button>
            </div>

            <!-- 知識庫 -->
            <div id="panel-knowledge" class="p-8 hidden">
                <div class="section-title">
                    <i class="fas fa-book text-indigo-500"></i> 知識庫
                </div>

                <p class="text-gray-600 mb-4">輸入你商店的相關資訊，AI 回覆時會參考這些內容。包含運費、退換貨政策、常見問題等。</p>

                <textarea id="cfg-knowledge" rows="18" class="input-field font-mono text-sm" placeholder="輸入知識庫內容..."></textarea>

                <button onclick="saveConfig()" class="btn btn-primary text-white px-8 py-4 rounded-xl font-bold mt-8">
                    <i class="fas fa-save mr-2"></i> 儲存知識庫
                </button>
            </div>
        </div>
    </main>

    <!-- Footer -->
    <footer class="text-center py-8 text-gray-400 text-sm">
        <p>蝦皮 AI 客服系統 &copy; 2024</p>
    </footer>

    <!-- Toast -->
    <div id="toast" class="fixed bottom-6 right-6 bg-green-500 text-white px-6 py-4 rounded-xl shadow-2xl transform translate-y-32 opacity-0 transition-all duration-300 flex items-center gap-3 z-50">
        <i class="fas fa-check-circle"></i>
        <span id="toast-msg">訊息</span>
    </div>

    <script>
        let config = {};

        // 載入設定
        async function loadConfig() {
            try {
                const res = await fetch('/api/config');
                config = await res.json();

                document.getElementById('api-key-status').textContent = config.api_key_display || '未設定';
                document.getElementById('api-key-status').className = config.openai_api_key ? 'font-medium text-green-600' : 'font-medium text-red-500';

                document.getElementById('cfg-model').value = config.openai_model || 'gpt-4o-mini';
                document.getElementById('cfg-url').value = config.shopee_chat_url || '';
                document.getElementById('cfg-refresh-min').value = config.refresh_min || 30;
                document.getElementById('cfg-refresh-max').value = config.refresh_max || 60;
                document.getElementById('cfg-typing-min').value = config.typing_min || 0.1;
                document.getElementById('cfg-typing-max').value = config.typing_max || 0.3;
                document.getElementById('cfg-send-min').value = config.send_wait_min || 1.0;
                document.getElementById('cfg-send-max').value = config.send_wait_max || 3.0;
                document.getElementById('cfg-prompt').value = config.system_prompt || '';
                document.getElementById('cfg-knowledge').value = config.knowledge_base || '';

                setToggle('toggle-auto-reply', config.auto_reply !== false);
                setToggle('toggle-typo', config.typo_simulation !== false);
                setToggle('toggle-kb', config.use_knowledge_base !== false);
            } catch (e) {
                console.error('載入設定失敗:', e);
            }
        }

        // 儲存設定
        async function saveConfig() {
            const apiKey = document.getElementById('cfg-api-key').value;

            const data = {
                openai_api_key: apiKey || config.openai_api_key || '',
                openai_model: document.getElementById('cfg-model').value,
                shopee_chat_url: document.getElementById('cfg-url').value,
                refresh_min: parseInt(document.getElementById('cfg-refresh-min').value) || 30,
                refresh_max: parseInt(document.getElementById('cfg-refresh-max').value) || 60,
                typing_min: parseFloat(document.getElementById('cfg-typing-min').value) || 0.1,
                typing_max: parseFloat(document.getElementById('cfg-typing-max').value) || 0.3,
                send_wait_min: parseFloat(document.getElementById('cfg-send-min').value) || 1.0,
                send_wait_max: parseFloat(document.getElementById('cfg-send-max').value) || 3.0,
                system_prompt: document.getElementById('cfg-prompt').value,
                knowledge_base: document.getElementById('cfg-knowledge').value,
                auto_reply: config.auto_reply !== false,
                typo_simulation: config.typo_simulation !== false,
                use_knowledge_base: config.use_knowledge_base !== false,
            };

            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });

                if (res.ok) {
                    showToast('設定已儲存！', true);
                    loadConfig();
                } else {
                    showToast('儲存失敗', false);
                }
            } catch (e) {
                showToast('儲存失敗: ' + e.message, false);
            }
        }

        // 開關
        function setToggle(id, value) {
            const el = document.getElementById(id);
            if (el) {
                if (value) el.classList.add('active');
                else el.classList.remove('active');
            }
        }

        function toggleSwitch(key) {
            config[key] = !config[key];
            const map = {
                'auto_reply': 'toggle-auto-reply',
                'typo_simulation': 'toggle-typo',
                'use_knowledge_base': 'toggle-kb'
            };
            setToggle(map[key], config[key]);
            saveConfig();
        }

        // 標籤頁
        function showTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('[id^="panel-"]').forEach(p => p.classList.add('hidden'));

            event.target.closest('.tab').classList.add('active');
            document.getElementById('panel-' + name).classList.remove('hidden');
        }

        // 下載
        function downloadEnv() {
            window.location.href = '/api/download-env';
            showToast('正在下載設定檔...', true);
        }

        function downloadKnowledge() {
            window.location.href = '/api/download-knowledge';
            showToast('正在下載知識庫...', true);
        }

        // Toast
        function showToast(msg, success = true) {
            const toast = document.getElementById('toast');
            const toastMsg = document.getElementById('toast-msg');
            toastMsg.textContent = msg;
            toast.className = `fixed bottom-6 right-6 ${success ? 'bg-green-500' : 'bg-red-500'} text-white px-6 py-4 rounded-xl shadow-2xl transform translate-y-0 opacity-100 transition-all duration-300 flex items-center gap-3 z-50`;

            setTimeout(() => {
                toast.className = 'fixed bottom-6 right-6 bg-green-500 text-white px-6 py-4 rounded-xl shadow-2xl transform translate-y-32 opacity-0 transition-all duration-300 flex items-center gap-3 z-50';
            }, 3000);
        }

        // 初始化
        loadConfig();
    </script>
</body>
</html>
"""


# ============================================
# 主程式
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\\n🚀 控制台啟動於 http://localhost:{port}\\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
