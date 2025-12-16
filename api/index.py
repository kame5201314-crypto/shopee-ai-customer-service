#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服系統 - Vercel Serverless 入口
支援 OpenAI 和 Google Gemini
"""

import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ============================================
# 設定儲存 (記憶體)
# ============================================

# 預設設定
DEFAULT_CONFIG = {
    "ai_provider": "openai",  # openai 或 gemini
    "openai_api_key": "",
    "gemini_api_key": "",
    "ai_model": "gpt-4o-mini",
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

# ============================================
# FastAPI
# ============================================

app = FastAPI(title="蝦皮 AI 客服控制台")


class ConfigModel(BaseModel):
    ai_provider: str = "openai"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
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
    # 遮蔽 OpenAI API Key
    if config.get("openai_api_key"):
        key = config["openai_api_key"]
        config["openai_api_key_display"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "已設定"
    else:
        config["openai_api_key_display"] = "未設定"
    # 遮蔽 Gemini API Key
    if config.get("gemini_api_key"):
        key = config["gemini_api_key"]
        config["gemini_api_key_display"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "已設定"
    else:
        config["gemini_api_key_display"] = "未設定"
    return config


@app.post("/api/config")
async def update_config(config: ConfigModel):
    global current_config
    data = config.model_dump()
    # 如果 API Key 為空，保留舊的
    if not data.get("openai_api_key"):
        data["openai_api_key"] = current_config.get("openai_api_key", "")
    if not data.get("gemini_api_key"):
        data["gemini_api_key"] = current_config.get("gemini_api_key", "")
    current_config.update(data)
    return {"success": True, "message": "設定已儲存"}


@app.get("/api/download-env")
async def download_env():
    """下載 .env 設定檔"""
    env_content = f"""# 蝦皮 AI 客服系統設定檔
# 請將此檔案放到專案目錄並命名為 .env

# AI 提供商 (openai 或 gemini)
AI_PROVIDER={current_config.get('ai_provider', 'openai')}

# OpenAI API Key
OPENAI_API_KEY={current_config.get('openai_api_key', '')}

# Google Gemini API Key
GEMINI_API_KEY={current_config.get('gemini_api_key', '')}

# AI 模型
AI_MODEL={current_config.get('ai_model', 'gpt-4o-mini')}

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
        .provider-btn { padding: 12px 24px; border: 2px solid #e5e7eb; border-radius: 12px; cursor: pointer; transition: 0.3s; font-weight: 600; }
        .provider-btn.active { border-color: #ee4d2d; background: #fef2f2; color: #ee4d2d; }
        .provider-btn:hover { border-color: #ee4d2d; }
        .api-section { border: 2px solid #e5e7eb; border-radius: 16px; padding: 20px; margin-top: 16px; }
        .api-section.active { border-color: #ee4d2d; background: #fffbfa; }
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
                    <i class="fas fa-brain text-purple-500"></i> AI 提供商選擇
                </div>

                <div class="flex gap-4 mb-6">
                    <div class="provider-btn active" id="provider-openai" onclick="selectProvider('openai')">
                        <i class="fas fa-robot mr-2"></i> OpenAI
                    </div>
                    <div class="provider-btn" id="provider-gemini" onclick="selectProvider('gemini')">
                        <i class="fas fa-gem mr-2"></i> Google Gemini
                    </div>
                </div>

                <!-- OpenAI 設定 -->
                <div id="openai-section" class="api-section active">
                    <div class="flex items-center gap-2 mb-4">
                        <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/OpenAI_Logo.svg/32px-OpenAI_Logo.svg.png" class="w-6 h-6" onerror="this.style.display='none'">
                        <h4 class="font-bold text-gray-800">OpenAI API 設定</h4>
                    </div>
                    <div class="space-y-4">
                        <div>
                            <label class="block font-medium text-gray-700 mb-2">OpenAI API Key</label>
                            <input type="password" id="cfg-openai-key" class="input-field" placeholder="sk-...">
                            <p class="help-text">目前狀態: <span id="openai-key-status" class="font-medium">檢查中...</span></p>
                            <p class="help-text">取得 API Key: <a href="https://platform.openai.com/api-keys" target="_blank" class="text-blue-500 hover:underline">platform.openai.com/api-keys</a></p>
                        </div>
                    </div>
                </div>

                <!-- Gemini 設定 -->
                <div id="gemini-section" class="api-section mt-4">
                    <div class="flex items-center gap-2 mb-4">
                        <i class="fas fa-gem text-blue-500 text-xl"></i>
                        <h4 class="font-bold text-gray-800">Google Gemini API 設定</h4>
                    </div>
                    <div class="space-y-4">
                        <div>
                            <label class="block font-medium text-gray-700 mb-2">Gemini API Key</label>
                            <input type="password" id="cfg-gemini-key" class="input-field" placeholder="AIza...">
                            <p class="help-text">目前狀態: <span id="gemini-key-status" class="font-medium">檢查中...</span></p>
                            <p class="help-text">取得 API Key: <a href="https://aistudio.google.com/app/apikey" target="_blank" class="text-blue-500 hover:underline">aistudio.google.com/app/apikey</a></p>
                        </div>
                    </div>
                </div>

                <!-- AI 模型選擇 -->
                <div class="mt-6">
                    <label class="block font-medium text-gray-700 mb-2">AI 模型</label>
                    <select id="cfg-model" class="input-field" onchange="updateModelInfo()">
                        <optgroup label="OpenAI 模型" id="openai-models">
                            <option value="gpt-4o-mini">GPT-4o Mini (推薦，便宜快速)</option>
                            <option value="gpt-4o">GPT-4o (更強，較貴)</option>
                            <option value="gpt-4-turbo">GPT-4 Turbo</option>
                            <option value="gpt-3.5-turbo">GPT-3.5 Turbo (最便宜)</option>
                        </optgroup>
                        <optgroup label="Google Gemini 模型" id="gemini-models">
                            <option value="gemini-2.0-flash-exp">Gemini 2.0 Flash (最新，推薦)</option>
                            <option value="gemini-1.5-pro">Gemini 1.5 Pro (最強)</option>
                            <option value="gemini-1.5-flash">Gemini 1.5 Flash (快速)</option>
                            <option value="gemini-1.5-flash-8b">Gemini 1.5 Flash-8B (最便宜)</option>
                        </optgroup>
                    </select>
                    <p class="help-text" id="model-info">選擇適合的 AI 模型</p>
                </div>

                <!-- 蝦皮網址 -->
                <div class="mt-6">
                    <label class="block font-medium text-gray-700 mb-2">蝦皮聊天頁面網址</label>
                    <input type="url" id="cfg-url" class="input-field" placeholder="https://seller.shopee.tw/portal/chatroom">
                    <p class="help-text">台灣蝦皮賣家中心聊天頁面</p>
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
        let currentProvider = 'openai';

        // 模型資訊
        const modelInfo = {
            'gpt-4o-mini': '推薦！快速且便宜，適合一般客服對話',
            'gpt-4o': '最強大的 OpenAI 模型，回覆品質最高',
            'gpt-4-turbo': '強大且快速，價格中等',
            'gpt-3.5-turbo': '最便宜的選擇，品質尚可',
            'gemini-2.0-flash-exp': '最新！Google 最新模型，速度快品質好',
            'gemini-1.5-pro': 'Google 最強模型，支援超長上下文',
            'gemini-1.5-flash': '快速且便宜，適合一般用途',
            'gemini-1.5-flash-8b': '最便宜的 Gemini 模型'
        };

        // 選擇 AI 提供商
        function selectProvider(provider) {
            currentProvider = provider;

            // 更新按鈕樣式
            document.getElementById('provider-openai').classList.toggle('active', provider === 'openai');
            document.getElementById('provider-gemini').classList.toggle('active', provider === 'gemini');

            // 更新區塊樣式
            document.getElementById('openai-section').classList.toggle('active', provider === 'openai');
            document.getElementById('gemini-section').classList.toggle('active', provider === 'gemini');

            // 自動選擇對應的模型
            const modelSelect = document.getElementById('cfg-model');
            if (provider === 'openai') {
                if (!modelSelect.value.startsWith('gpt')) {
                    modelSelect.value = 'gpt-4o-mini';
                }
            } else {
                if (!modelSelect.value.startsWith('gemini')) {
                    modelSelect.value = 'gemini-2.0-flash-exp';
                }
            }
            updateModelInfo();
        }

        // 更新模型說明
        function updateModelInfo() {
            const model = document.getElementById('cfg-model').value;
            document.getElementById('model-info').textContent = modelInfo[model] || '選擇適合的 AI 模型';

            // 自動切換提供商
            if (model.startsWith('gpt') && currentProvider !== 'openai') {
                selectProvider('openai');
            } else if (model.startsWith('gemini') && currentProvider !== 'gemini') {
                selectProvider('gemini');
            }
        }

        // 載入設定
        async function loadConfig() {
            try {
                const res = await fetch('/api/config');
                config = await res.json();

                // API Key 狀態
                document.getElementById('openai-key-status').textContent = config.openai_api_key_display || '未設定';
                document.getElementById('openai-key-status').className = config.openai_api_key ? 'font-medium text-green-600' : 'font-medium text-red-500';

                document.getElementById('gemini-key-status').textContent = config.gemini_api_key_display || '未設定';
                document.getElementById('gemini-key-status').className = config.gemini_api_key ? 'font-medium text-green-600' : 'font-medium text-red-500';

                // 選擇提供商
                selectProvider(config.ai_provider || 'openai');

                document.getElementById('cfg-model').value = config.ai_model || 'gpt-4o-mini';
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

                updateModelInfo();
            } catch (e) {
                console.error('載入設定失敗:', e);
            }
        }

        // 儲存設定
        async function saveConfig() {
            const openaiKey = document.getElementById('cfg-openai-key').value;
            const geminiKey = document.getElementById('cfg-gemini-key').value;

            const data = {
                ai_provider: currentProvider,
                openai_api_key: openaiKey || config.openai_api_key || '',
                gemini_api_key: geminiKey || config.gemini_api_key || '',
                ai_model: document.getElementById('cfg-model').value,
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
