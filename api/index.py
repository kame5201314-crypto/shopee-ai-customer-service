#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服系統 - 安全強化版
支援 OpenAI 和 Google Gemini

安全功能：
1. 管理員認證機制 (Session-based)
2. 密碼 Hash 處理 (SHA-256 + Salt)
3. API Rate Limiting (防暴力破解)
4. 審計日誌 (Audit Log)
5. 安全 HTTP Headers
6. 前端不暴露完整 API Key
7. CSRF 保護
8. 環境變數管理機密資訊
"""

import json
import os
import hashlib
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Response, Request, Depends, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============================================
# 安全配置
# ============================================

# 從環境變數讀取管理員密碼 (預設: admin123，生產環境必須更改！)
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_SALT = os.environ.get("ADMIN_SALT", "shopee-ai-secure-salt-2024")

# Session 配置
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
SESSION_EXPIRE_HOURS = 24

# Rate Limiting 配置
MAX_LOGIN_ATTEMPTS = 5  # 最大登入嘗試次數
LOGIN_LOCKOUT_MINUTES = 15  # 鎖定時間（分鐘）
API_RATE_LIMIT = 60  # 每分鐘最大 API 呼叫次數

# ============================================
# 安全工具函數
# ============================================

def hash_password(password: str, salt: str = ADMIN_SALT) -> str:
    """使用 SHA-256 + Salt 進行密碼 Hash"""
    return hashlib.sha256(f"{salt}{password}{salt}".encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    """驗證密碼"""
    return hash_password(password) == hashed

def generate_session_token() -> str:
    """生成安全的 Session Token"""
    return secrets.token_urlsafe(32)

def mask_api_key(key: str) -> str:
    """遮蔽 API Key，只顯示前4後4"""
    if not key or len(key) < 12:
        return "未設定" if not key else "已設定"
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"

# ============================================
# 記憶體儲存 (生產環境應使用資料庫)
# ============================================

# Session 儲存
sessions: Dict[str, Dict[str, Any]] = {}

# 登入嘗試記錄 (IP -> {attempts, lockout_until})
login_attempts: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"attempts": 0, "lockout_until": None})

# API 呼叫記錄 (IP -> [timestamps])
api_calls: Dict[str, list] = defaultdict(list)

# 審計日誌
audit_logs: list = []

# 配置儲存
DEFAULT_CONFIG = {
    "ai_provider": "openai",
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

current_config = DEFAULT_CONFIG.copy()

# 如果沒有設定管理員密碼，使用預設密碼的 hash
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = hash_password("admin123")

# ============================================
# 審計日誌功能
# ============================================

def add_audit_log(
    action: str,
    ip: str,
    user: str = "anonymous",
    details: str = "",
    success: bool = True
):
    """新增審計日誌"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "user": user,
        "ip": ip,
        "details": details,
        "success": success
    }
    audit_logs.append(log_entry)
    # 只保留最近 1000 筆日誌
    if len(audit_logs) > 1000:
        audit_logs.pop(0)
    # 同時輸出到 console (生產環境應寫入檔案或日誌服務)
    status = "SUCCESS" if success else "FAILED"
    print(f"[AUDIT] {log_entry['timestamp']} | {status} | {action} | IP: {ip} | User: {user} | {details}")

# ============================================
# Rate Limiting
# ============================================

def check_rate_limit(ip: str, limit: int = API_RATE_LIMIT) -> bool:
    """檢查 API 呼叫頻率"""
    now = time.time()
    minute_ago = now - 60

    # 清理過期記錄
    api_calls[ip] = [t for t in api_calls[ip] if t > minute_ago]

    if len(api_calls[ip]) >= limit:
        return False

    api_calls[ip].append(now)
    return True

def check_login_lockout(ip: str) -> tuple[bool, int]:
    """檢查登入鎖定狀態"""
    record = login_attempts[ip]

    if record["lockout_until"]:
        if datetime.now() < record["lockout_until"]:
            remaining = (record["lockout_until"] - datetime.now()).seconds
            return True, remaining
        else:
            # 解除鎖定
            record["attempts"] = 0
            record["lockout_until"] = None

    return False, 0

def record_login_attempt(ip: str, success: bool):
    """記錄登入嘗試"""
    if success:
        login_attempts[ip] = {"attempts": 0, "lockout_until": None}
    else:
        login_attempts[ip]["attempts"] += 1
        if login_attempts[ip]["attempts"] >= MAX_LOGIN_ATTEMPTS:
            login_attempts[ip]["lockout_until"] = datetime.now() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)

# ============================================
# Session 管理
# ============================================

def create_session(ip: str) -> str:
    """建立新 Session"""
    token = generate_session_token()
    sessions[token] = {
        "ip": ip,
        "created_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(hours=SESSION_EXPIRE_HOURS)
    }
    return token

def verify_session(token: str, ip: str) -> bool:
    """驗證 Session"""
    if not token or token not in sessions:
        return False

    session = sessions[token]

    # 檢查是否過期
    if datetime.now() > session["expires_at"]:
        del sessions[token]
        return False

    # 檢查 IP 是否匹配 (防止 Session 劫持)
    # 注意：某些情況下 IP 可能會變化，可以根據需求調整
    # if session["ip"] != ip:
    #     return False

    return True

def invalidate_session(token: str):
    """登出，使 Session 失效"""
    if token in sessions:
        del sessions[token]

# ============================================
# FastAPI 應用
# ============================================

app = FastAPI(title="蝦皮 AI 客服控制台 - 安全版")

# CORS 設定 (生產環境應限制來源)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生產環境應改為特定域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# 安全中間件
# ============================================

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """安全中間件：Rate Limiting + 安全 Headers"""
    ip = request.client.host if request.client else "unknown"

    # Rate Limiting (排除靜態資源)
    if not request.url.path.startswith("/static"):
        if not check_rate_limit(ip):
            add_audit_log("RATE_LIMIT_EXCEEDED", ip, details=f"Path: {request.url.path}", success=False)
            return JSONResponse(
                status_code=429,
                content={"error": "請求過於頻繁，請稍後再試"}
            )

    response = await call_next(request)

    # 安全 HTTP Headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; font-src https://cdnjs.cloudflare.com; img-src 'self' data: https:;"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    return response

# ============================================
# 認證相關 API
# ============================================

class LoginRequest(BaseModel):
    password: str

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

def get_client_ip(request: Request) -> str:
    """取得客戶端 IP"""
    # 處理 proxy 情況
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

async def verify_auth(request: Request, session_token: Optional[str] = Cookie(None, alias="session_token")) -> bool:
    """驗證認證狀態"""
    ip = get_client_ip(request)
    if not session_token or not verify_session(session_token, ip):
        return False
    return True

@app.post("/api/login")
async def login(request: Request, login_data: LoginRequest):
    """管理員登入"""
    ip = get_client_ip(request)

    # 檢查是否被鎖定
    is_locked, remaining_seconds = check_login_lockout(ip)
    if is_locked:
        add_audit_log("LOGIN_BLOCKED", ip, details=f"Account locked for {remaining_seconds} seconds", success=False)
        raise HTTPException(
            status_code=423,
            detail=f"帳號已被鎖定，請在 {remaining_seconds // 60 + 1} 分鐘後再試"
        )

    # 驗證密碼
    if not verify_password(login_data.password, ADMIN_PASSWORD_HASH):
        record_login_attempt(ip, False)
        remaining = MAX_LOGIN_ATTEMPTS - login_attempts[ip]["attempts"]
        add_audit_log("LOGIN_FAILED", ip, details=f"Remaining attempts: {remaining}", success=False)
        raise HTTPException(
            status_code=401,
            detail=f"密碼錯誤，剩餘 {remaining} 次嘗試機會"
        )

    # 登入成功
    record_login_attempt(ip, True)
    session_token = create_session(ip)
    add_audit_log("LOGIN_SUCCESS", ip, user="admin")

    response = JSONResponse(content={"success": True, "message": "登入成功"})
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,  # 防止 XSS 攻擊
        secure=True,    # 只在 HTTPS 傳輸
        samesite="strict",  # 防止 CSRF
        max_age=SESSION_EXPIRE_HOURS * 3600
    )
    return response

@app.post("/api/logout")
async def logout(request: Request, session_token: Optional[str] = Cookie(None, alias="session_token")):
    """登出"""
    ip = get_client_ip(request)

    if session_token:
        invalidate_session(session_token)

    add_audit_log("LOGOUT", ip, user="admin")

    response = JSONResponse(content={"success": True, "message": "已登出"})
    response.delete_cookie("session_token")
    return response

@app.get("/api/auth/status")
async def auth_status(request: Request, session_token: Optional[str] = Cookie(None, alias="session_token")):
    """檢查認證狀態"""
    ip = get_client_ip(request)
    is_authenticated = await verify_auth(request, session_token)
    return {"authenticated": is_authenticated}

# ============================================
# 配置相關 API (需要認證)
# ============================================

@app.get("/api/config")
async def get_config(request: Request, session_token: Optional[str] = Cookie(None, alias="session_token")):
    """取得配置 (需要認證)"""
    ip = get_client_ip(request)

    if not await verify_auth(request, session_token):
        add_audit_log("GET_CONFIG_UNAUTHORIZED", ip, success=False)
        raise HTTPException(status_code=401, detail="請先登入")

    config = current_config.copy()

    # 遮蔽 API Key (安全：永遠不返回完整的 API Key)
    config["openai_api_key_display"] = mask_api_key(config.get("openai_api_key", ""))
    config["gemini_api_key_display"] = mask_api_key(config.get("gemini_api_key", ""))

    # 不返回實際的 API Key 到前端
    config["openai_api_key"] = ""
    config["gemini_api_key"] = ""

    # 標記 API Key 是否已設定
    config["openai_api_key_set"] = bool(current_config.get("openai_api_key"))
    config["gemini_api_key_set"] = bool(current_config.get("gemini_api_key"))

    add_audit_log("GET_CONFIG", ip, user="admin")
    return config

@app.post("/api/config")
async def update_config(request: Request, config: ConfigModel, session_token: Optional[str] = Cookie(None, alias="session_token")):
    """更新配置 (需要認證)"""
    global current_config
    ip = get_client_ip(request)

    if not await verify_auth(request, session_token):
        add_audit_log("UPDATE_CONFIG_UNAUTHORIZED", ip, success=False)
        raise HTTPException(status_code=401, detail="請先登入")

    data = config.model_dump()

    # 如果 API Key 為空，保留舊的
    if not data.get("openai_api_key"):
        data["openai_api_key"] = current_config.get("openai_api_key", "")
    if not data.get("gemini_api_key"):
        data["gemini_api_key"] = current_config.get("gemini_api_key", "")

    # 記錄變更
    changes = []
    for key, value in data.items():
        if key in ["openai_api_key", "gemini_api_key"]:
            if value != current_config.get(key):
                changes.append(f"{key}: [changed]")
        elif value != current_config.get(key):
            changes.append(f"{key}: {current_config.get(key)} -> {value}")

    current_config.update(data)

    add_audit_log("UPDATE_CONFIG", ip, user="admin", details="; ".join(changes) if changes else "No changes")
    return {"success": True, "message": "設定已儲存"}

@app.get("/api/download-env")
async def download_env(request: Request, session_token: Optional[str] = Cookie(None, alias="session_token")):
    """下載 .env 設定檔 (需要認證)"""
    ip = get_client_ip(request)

    if not await verify_auth(request, session_token):
        add_audit_log("DOWNLOAD_ENV_UNAUTHORIZED", ip, success=False)
        raise HTTPException(status_code=401, detail="請先登入")

    env_content = f"""# 蝦皮 AI 客服系統設定檔
# 請將此檔案放到專案目錄並命名為 .env
# 警告：此檔案包含敏感資訊，請勿上傳到 GitHub！

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

    add_audit_log("DOWNLOAD_ENV", ip, user="admin")

    return Response(
        content=env_content,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=.env"}
    )

@app.get("/api/download-knowledge")
async def download_knowledge(request: Request, session_token: Optional[str] = Cookie(None, alias="session_token")):
    """下載知識庫 (需要認證)"""
    ip = get_client_ip(request)

    if not await verify_auth(request, session_token):
        add_audit_log("DOWNLOAD_KNOWLEDGE_UNAUTHORIZED", ip, success=False)
        raise HTTPException(status_code=401, detail="請先登入")

    content = current_config.get("knowledge_base", "")

    add_audit_log("DOWNLOAD_KNOWLEDGE", ip, user="admin")

    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=knowledge_base.txt"}
    )

# ============================================
# 審計日誌 API (需要認證)
# ============================================

@app.get("/api/audit-logs")
async def get_audit_logs(request: Request, session_token: Optional[str] = Cookie(None, alias="session_token"), limit: int = 100):
    """取得審計日誌 (需要認證)"""
    ip = get_client_ip(request)

    if not await verify_auth(request, session_token):
        add_audit_log("GET_AUDIT_LOGS_UNAUTHORIZED", ip, success=False)
        raise HTTPException(status_code=401, detail="請先登入")

    add_audit_log("VIEW_AUDIT_LOGS", ip, user="admin")

    # 返回最近的日誌，最新的在前面
    return {"logs": list(reversed(audit_logs[-limit:]))}

# ============================================
# 主頁面
# ============================================

@app.get("/", response_class=HTMLResponse)
async def index():
    return DASHBOARD_HTML

# ============================================
# HTML 模板 (包含登入頁面)
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
        .btn-red { background: linear-gradient(135deg, #ef4444 0%, #f87171 100%); }
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
        .login-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .security-badge { background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
        .log-entry { border-left: 3px solid #e5e7eb; padding-left: 12px; margin-bottom: 8px; }
        .log-entry.success { border-color: #10b981; }
        .log-entry.failed { border-color: #ef4444; }
    </style>
</head>
<body class="bg-gray-50 min-h-screen">
    <!-- 登入覆蓋層 -->
    <div id="login-overlay" class="login-overlay">
        <div class="card p-8 w-full max-w-md mx-4">
            <div class="text-center mb-6">
                <div class="w-20 h-20 gradient-bg rounded-2xl flex items-center justify-center mx-auto mb-4">
                    <i class="fas fa-lock text-white text-3xl"></i>
                </div>
                <h2 class="text-2xl font-bold text-gray-800">管理員登入</h2>
                <p class="text-gray-500 mt-2">請輸入管理員密碼</p>
            </div>

            <div class="space-y-4">
                <div>
                    <label class="block font-medium text-gray-700 mb-2">密碼</label>
                    <input type="password" id="login-password" class="input-field" placeholder="請輸入密碼" onkeypress="if(event.key==='Enter')login()">
                </div>
                <div id="login-error" class="text-red-500 text-sm hidden"></div>
                <button onclick="login()" class="btn btn-primary text-white w-full py-4 rounded-xl font-bold text-lg">
                    <i class="fas fa-sign-in-alt mr-2"></i> 登入
                </button>
            </div>

            <div class="mt-6 p-4 bg-yellow-50 rounded-xl">
                <p class="text-yellow-800 text-sm">
                    <i class="fas fa-exclamation-triangle mr-2"></i>
                    <strong>安全提示：</strong>預設密碼為 admin123，請在 Vercel 環境變數中設定 ADMIN_PASSWORD_HASH 來更改密碼。
                </p>
            </div>
        </div>
    </div>

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
                        <p class="text-white/80 mt-1">
                            <span class="security-badge"><i class="fas fa-shield-alt mr-1"></i>安全強化版</span>
                        </p>
                    </div>
                </div>
                <button id="logout-btn" onclick="logout()" class="btn bg-white/20 text-white px-6 py-3 rounded-xl font-bold hidden">
                    <i class="fas fa-sign-out-alt mr-2"></i> 登出
                </button>
            </div>
        </div>
    </header>

    <main id="main-content" class="max-w-5xl mx-auto px-6 py-8 hidden">
        <!-- 安全提示卡片 -->
        <div class="card p-6 mb-8 border-l-4 border-green-500">
            <div class="flex items-start gap-4">
                <div class="w-12 h-12 bg-green-100 rounded-xl flex items-center justify-center flex-shrink-0">
                    <i class="fas fa-shield-alt text-green-500 text-xl"></i>
                </div>
                <div>
                    <h3 class="font-bold text-gray-800 text-lg">安全功能已啟用</h3>
                    <p class="text-gray-600 mt-2">
                        <i class="fas fa-check text-green-500 mr-2"></i>管理員認證<br>
                        <i class="fas fa-check text-green-500 mr-2"></i>API 呼叫頻率限制<br>
                        <i class="fas fa-check text-green-500 mr-2"></i>審計日誌記錄<br>
                        <i class="fas fa-check text-green-500 mr-2"></i>敏感資料遮蔽
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
                <div class="tab" onclick="showTab('logs')"><i class="fas fa-history mr-2"></i>審計日誌</div>
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
                        <i class="fas fa-robot text-green-600 text-xl"></i>
                        <h4 class="font-bold text-gray-800">OpenAI API 設定</h4>
                    </div>
                    <div class="space-y-4">
                        <div>
                            <label class="block font-medium text-gray-700 mb-2">OpenAI API Key</label>
                            <input type="password" id="cfg-openai-key" class="input-field" placeholder="sk-... (輸入新的 API Key 來更新)">
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
                            <input type="password" id="cfg-gemini-key" class="input-field" placeholder="AIza... (輸入新的 API Key 來更新)">
                            <p class="help-text">目前狀態: <span id="gemini-key-status" class="font-medium">檢查中...</span></p>
                            <p class="help-text">取得 API Key: <a href="https://aistudio.google.com/app/apikey" target="_blank" class="text-blue-500 hover:underline">aistudio.google.com/app/apikey</a></p>
                        </div>
                    </div>
                </div>

                <!-- AI 模型選擇 -->
                <div class="mt-6">
                    <label class="block font-medium text-gray-700 mb-2">AI 模型</label>
                    <select id="cfg-model" class="input-field" onchange="updateModelInfo()">
                        <optgroup label="OpenAI 模型">
                            <option value="gpt-4o-mini">GPT-4o Mini (推薦，便宜快速)</option>
                            <option value="gpt-4o">GPT-4o (更強，較貴)</option>
                            <option value="gpt-4-turbo">GPT-4 Turbo</option>
                            <option value="gpt-3.5-turbo">GPT-3.5 Turbo (最便宜)</option>
                        </optgroup>
                        <optgroup label="Google Gemini 模型">
                            <option value="gemini-2.5-flash-preview-05-20">Gemini 2.5 Flash (最新，推薦)</option>
                            <option value="gemini-2.0-flash">Gemini 2.0 Flash (穩定版)</option>
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
                        <p class="help-text">每個字元輸入的間隔，模擬真人打字</p>
                    </div>

                    <div>
                        <label class="block font-medium text-gray-700 mb-3">發送前等待 (秒)</label>
                        <div class="flex items-center gap-4">
                            <input type="number" step="0.5" id="cfg-send-min" class="input-field w-32" value="1.0">
                            <span class="text-gray-400 text-xl">~</span>
                            <input type="number" step="0.5" id="cfg-send-max" class="input-field w-32" value="3.0">
                            <span class="text-gray-500">秒</span>
                        </div>
                        <p class="help-text">打完字後等待一段時間再發送</p>
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

                <p class="text-gray-600 mb-4">設定 AI 的角色和回覆風格</p>

                <textarea id="cfg-prompt" rows="10" class="input-field font-mono" placeholder="輸入 AI 系統提示詞..."></textarea>

                <button onclick="saveConfig()" class="btn btn-primary text-white px-8 py-4 rounded-xl font-bold mt-8">
                    <i class="fas fa-save mr-2"></i> 儲存設定
                </button>
            </div>

            <!-- 知識庫 -->
            <div id="panel-knowledge" class="p-8 hidden">
                <div class="section-title">
                    <i class="fas fa-book text-indigo-500"></i> 知識庫
                </div>

                <p class="text-gray-600 mb-4">輸入商店相關資訊，AI 回覆時會參考這些內容</p>

                <textarea id="cfg-knowledge" rows="18" class="input-field font-mono text-sm" placeholder="輸入知識庫內容..."></textarea>

                <button onclick="saveConfig()" class="btn btn-primary text-white px-8 py-4 rounded-xl font-bold mt-8">
                    <i class="fas fa-save mr-2"></i> 儲存知識庫
                </button>
            </div>

            <!-- 審計日誌 -->
            <div id="panel-logs" class="p-8 hidden">
                <div class="section-title">
                    <i class="fas fa-history text-gray-500"></i> 審計日誌
                </div>

                <p class="text-gray-600 mb-4">系統操作記錄，用於追蹤問題和安全審計</p>

                <button onclick="loadAuditLogs()" class="btn btn-blue text-white px-6 py-3 rounded-xl font-bold mb-4">
                    <i class="fas fa-sync mr-2"></i> 重新載入
                </button>

                <div id="audit-logs-container" class="bg-gray-50 rounded-xl p-4 max-h-96 overflow-y-auto font-mono text-sm">
                    <p class="text-gray-500">載入中...</p>
                </div>
            </div>
        </div>
    </main>

    <!-- Footer -->
    <footer class="text-center py-8 text-gray-400 text-sm">
        <p>蝦皮 AI 客服系統 - 安全強化版 &copy; 2024</p>
    </footer>

    <!-- Toast -->
    <div id="toast" class="fixed bottom-6 right-6 bg-green-500 text-white px-6 py-4 rounded-xl shadow-2xl transform translate-y-32 opacity-0 transition-all duration-300 flex items-center gap-3 z-50">
        <i class="fas fa-check-circle"></i>
        <span id="toast-msg">訊息</span>
    </div>

    <script>
        let config = {};
        let currentProvider = 'openai';
        let isAuthenticated = false;

        const modelInfo = {
            'gpt-4o-mini': '推薦！快速且便宜，適合一般客服對話',
            'gpt-4o': '最強大的 OpenAI 模型，回覆品質最高',
            'gpt-4-turbo': '強大且快速，價格中等',
            'gpt-3.5-turbo': '最便宜的選擇，品質尚可',
            'gemini-2.5-flash-preview-05-20': '最新！Gemini 2.5 Flash，性能最強',
            'gemini-2.0-flash': 'Gemini 2.0 穩定版，速度快品質好',
            'gemini-1.5-pro': 'Google 最強模型，支援超長上下文',
            'gemini-1.5-flash': '快速且便宜，適合一般用途',
            'gemini-1.5-flash-8b': '最便宜的 Gemini 模型'
        };

        // 檢查認證狀態
        async function checkAuth() {
            try {
                const res = await fetch('/api/auth/status', { credentials: 'include' });
                const data = await res.json();
                isAuthenticated = data.authenticated;
                updateUI();
            } catch (e) {
                console.error('檢查認證狀態失敗:', e);
            }
        }

        function updateUI() {
            document.getElementById('login-overlay').style.display = isAuthenticated ? 'none' : 'flex';
            document.getElementById('main-content').classList.toggle('hidden', !isAuthenticated);
            document.getElementById('logout-btn').classList.toggle('hidden', !isAuthenticated);

            if (isAuthenticated) {
                loadConfig();
            }
        }

        // 登入
        async function login() {
            const password = document.getElementById('login-password').value;
            const errorDiv = document.getElementById('login-error');

            if (!password) {
                errorDiv.textContent = '請輸入密碼';
                errorDiv.classList.remove('hidden');
                return;
            }

            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password }),
                    credentials: 'include'
                });

                const data = await res.json();

                if (res.ok) {
                    isAuthenticated = true;
                    errorDiv.classList.add('hidden');
                    document.getElementById('login-password').value = '';
                    updateUI();
                    showToast('登入成功！', true);
                } else {
                    errorDiv.textContent = data.detail || '登入失敗';
                    errorDiv.classList.remove('hidden');
                }
            } catch (e) {
                errorDiv.textContent = '連線錯誤，請稍後再試';
                errorDiv.classList.remove('hidden');
            }
        }

        // 登出
        async function logout() {
            try {
                await fetch('/api/logout', { method: 'POST', credentials: 'include' });
                isAuthenticated = false;
                updateUI();
                showToast('已登出', true);
            } catch (e) {
                console.error('登出失敗:', e);
            }
        }

        function selectProvider(provider) {
            currentProvider = provider;
            document.getElementById('provider-openai').classList.toggle('active', provider === 'openai');
            document.getElementById('provider-gemini').classList.toggle('active', provider === 'gemini');
            document.getElementById('openai-section').classList.toggle('active', provider === 'openai');
            document.getElementById('gemini-section').classList.toggle('active', provider === 'gemini');

            const modelSelect = document.getElementById('cfg-model');
            if (provider === 'openai' && !modelSelect.value.startsWith('gpt')) {
                modelSelect.value = 'gpt-4o-mini';
            } else if (provider === 'gemini' && !modelSelect.value.startsWith('gemini')) {
                modelSelect.value = 'gemini-2.5-flash-preview-05-20';
            }
            updateModelInfo();
        }

        function updateModelInfo() {
            const model = document.getElementById('cfg-model').value;
            document.getElementById('model-info').textContent = modelInfo[model] || '選擇適合的 AI 模型';
            if (model.startsWith('gpt') && currentProvider !== 'openai') selectProvider('openai');
            else if (model.startsWith('gemini') && currentProvider !== 'gemini') selectProvider('gemini');
        }

        async function loadConfig() {
            try {
                const res = await fetch('/api/config', { credentials: 'include' });
                if (!res.ok) {
                    if (res.status === 401) {
                        isAuthenticated = false;
                        updateUI();
                    }
                    return;
                }
                config = await res.json();

                // API Key 狀態顯示 (只顯示遮蔽後的版本)
                document.getElementById('openai-key-status').textContent = config.openai_api_key_display || '未設定';
                document.getElementById('openai-key-status').className = config.openai_api_key_set ? 'font-medium text-green-600' : 'font-medium text-red-500';

                document.getElementById('gemini-key-status').textContent = config.gemini_api_key_display || '未設定';
                document.getElementById('gemini-key-status').className = config.gemini_api_key_set ? 'font-medium text-green-600' : 'font-medium text-red-500';

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

        async function saveConfig() {
            const openaiKey = document.getElementById('cfg-openai-key').value;
            const geminiKey = document.getElementById('cfg-gemini-key').value;

            const data = {
                ai_provider: currentProvider,
                openai_api_key: openaiKey,  // 只有輸入新的才會更新
                gemini_api_key: geminiKey,
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
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data),
                    credentials: 'include'
                });

                if (res.ok) {
                    showToast('設定已儲存！', true);
                    // 清空 API Key 輸入欄
                    document.getElementById('cfg-openai-key').value = '';
                    document.getElementById('cfg-gemini-key').value = '';
                    loadConfig();
                } else if (res.status === 401) {
                    isAuthenticated = false;
                    updateUI();
                    showToast('請重新登入', false);
                } else {
                    showToast('儲存失敗', false);
                }
            } catch (e) {
                showToast('儲存失敗: ' + e.message, false);
            }
        }

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

        function showTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('[id^="panel-"]').forEach(p => p.classList.add('hidden'));
            event.target.closest('.tab').classList.add('active');
            document.getElementById('panel-' + name).classList.remove('hidden');

            if (name === 'logs') loadAuditLogs();
        }

        function downloadEnv() {
            window.location.href = '/api/download-env';
            showToast('正在下載設定檔...', true);
        }

        function downloadKnowledge() {
            window.location.href = '/api/download-knowledge';
            showToast('正在下載知識庫...', true);
        }

        async function loadAuditLogs() {
            const container = document.getElementById('audit-logs-container');
            try {
                const res = await fetch('/api/audit-logs?limit=50', { credentials: 'include' });
                if (!res.ok) {
                    container.innerHTML = '<p class="text-red-500">載入失敗</p>';
                    return;
                }
                const data = await res.json();

                if (data.logs.length === 0) {
                    container.innerHTML = '<p class="text-gray-500">暫無日誌</p>';
                    return;
                }

                container.innerHTML = data.logs.map(log => `
                    <div class="log-entry ${log.success ? 'success' : 'failed'} mb-3">
                        <div class="flex justify-between items-start">
                            <span class="font-bold ${log.success ? 'text-green-600' : 'text-red-600'}">${log.action}</span>
                            <span class="text-gray-400 text-xs">${log.timestamp}</span>
                        </div>
                        <div class="text-gray-600 text-xs mt-1">
                            IP: ${log.ip} | User: ${log.user}
                            ${log.details ? `<br>Details: ${log.details}` : ''}
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                container.innerHTML = '<p class="text-red-500">載入失敗: ' + e.message + '</p>';
            }
        }

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
        checkAuth();
    </script>
</body>
</html>
"""
