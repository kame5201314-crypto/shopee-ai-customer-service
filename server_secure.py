#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服系統 - 安全強化版
包含完整的資安機制

資安功能：
1. API Key 安全管理（永不暴露到前端）
2. 使用者認證與授權（密碼雜湊儲存）
3. Session 管理（帶過期機制）
4. 速率限制（防暴力破解）
5. 審計日誌（追蹤所有操作）
6. SQLite 持久化儲存（非暫存）
7. 安全 HTTP 標頭
8. 輸入驗證與清理
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import uvicorn

# 載入環境變數
load_dotenv()

# 安全模組
from security import (
    rate_limiter,
    InputValidator,
    PasswordSecurity,
    get_security_headers,
    key_manager,
    init_security
)

# 資料庫模組
from database import get_database, init_default_admin

# 認證模組
from auth import (
    get_auth_service,
    get_current_user,
    require_login,
    require_admin,
    AuthMiddleware,
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
    AuditMiddleware
)

# Gemini 服務
from gemini_service import get_gemini_service, initialize_gemini, generate_reply

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/app.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 確保日誌目錄存在
Path('logs').mkdir(exist_ok=True)
Path('data').mkdir(exist_ok=True)


# ============================================
# Pydantic 模型
# ============================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8)


class TestMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class SettingsUpdate(BaseModel):
    gemini_model: str = None
    products_file: str = None
    faq_file: str = None
    cache_ttl_hours: int = None
    auto_reply: bool = None


# ============================================
# 應用程式初始化
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用程式生命週期管理"""
    logger.info("=" * 50)
    logger.info("蝦皮 AI 客服系統（安全版）啟動中...")
    logger.info("=" * 50)

    # 初始化安全模組
    init_security()

    # 初始化資料庫
    db = get_database()
    init_default_admin()

    # 初始化 Gemini
    gemini_key = os.getenv('GEMINI_API_KEY')
    if gemini_key:
        try:
            key_manager.set_key('gemini', gemini_key)
            success = initialize_gemini()
            if success:
                logger.info("Gemini Context Cache 初始化成功")
            else:
                logger.warning("Gemini 初始化失敗，使用無快取模式")
        except Exception as e:
            logger.error(f"Gemini 初始化錯誤: {e}")
    else:
        logger.warning("未設定 GEMINI_API_KEY")

    # 每日備份資料庫
    db.backup()

    logger.info("系統初始化完成")

    yield

    # 關閉時清理
    logger.info("系統關閉中...")


# 建立 FastAPI 應用
app = FastAPI(
    title="蝦皮 AI 客服系統",
    description="安全強化版",
    version="2.0.0",
    lifespan=lifespan
)

# 添加中間件（順序重要）
app.add_middleware(AuditMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# 注意：AuthMiddleware 需要手動添加公開路徑


# ============================================
# 公開端點（不需認證）
# ============================================

@app.get("/", response_class=HTMLResponse)
async def index():
    """首頁（重定向到登入或控制台）"""
    return RedirectResponse(url="/login")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """登入頁面"""
    return LOGIN_HTML


@app.post("/api/login")
async def login(request: Request, login_data: LoginRequest):
    """登入 API"""
    # 取得客戶端資訊
    ip = request.headers.get('X-Forwarded-For', request.client.host if request.client else 'unknown')
    user_agent = request.headers.get('User-Agent', '')

    # 清理輸入
    username = InputValidator.sanitize_string(login_data.username)

    # 執行登入
    auth_service = get_auth_service()
    result = auth_service.login(
        username=username,
        password=login_data.password,
        ip_address=ip,
        user_agent=user_agent
    )

    if result['success']:
        # 建立回應並設定 Cookie
        response = JSONResponse(content={
            'success': True,
            'message': '登入成功',
            'user': result['user']
        })

        # 安全的 Cookie 設定
        response.set_cookie(
            key='session_id',
            value=result['session_id'],
            httponly=True,  # 防止 XSS
            secure=os.getenv('SECURE_COOKIE', 'false').lower() == 'true',  # HTTPS only
            samesite='strict',  # 防止 CSRF
            max_age=3600  # 1 小時
        )

        return response
    else:
        # 記錄失敗嘗試
        if 'attempts' in result:
            rate_limiter.record_failed_attempt(ip)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result['message']
        )


@app.post("/api/logout")
async def logout(request: Request):
    """登出 API"""
    session_id = request.cookies.get('session_id')
    ip = request.headers.get('X-Forwarded-For', request.client.host if request.client else 'unknown')

    if session_id:
        auth_service = get_auth_service()
        auth_service.logout(session_id, ip)

    response = JSONResponse(content={'success': True, 'message': '已登出'})
    response.delete_cookie('session_id')
    return response


@app.get("/api/health")
async def health_check():
    """健康檢查"""
    return {'status': 'ok', 'timestamp': datetime.now().isoformat()}


# ============================================
# 需要認證的端點
# ============================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(user: dict = Depends(get_current_user)):
    """控制台頁面"""
    if not user:
        return RedirectResponse(url="/login")
    return DASHBOARD_HTML


@app.get("/api/me")
async def get_me(user: dict = Depends(require_login)):
    """取得當前使用者資訊"""
    return {
        'user': user,
        'permissions': _get_user_permissions(user['role'])
    }


@app.post("/api/change-password")
async def change_password(
    request: ChangePasswordRequest,
    user: dict = Depends(require_login)
):
    """變更密碼"""
    auth_service = get_auth_service()
    result = auth_service.change_password(
        user_id=user['user_id'],
        old_password=request.old_password,
        new_password=request.new_password
    )

    if result['success']:
        return result
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result['message']
        )


@app.get("/api/status")
async def get_status(user: dict = Depends(require_login)):
    """系統狀態"""
    db = get_database()

    return {
        'system': 'running',
        'user': user['username'],
        'role': user['role'],
        'gemini': {
            'api_key_set': key_manager.has_key('gemini'),
            'api_key_display': key_manager.get_masked_key('gemini')
        },
        'stats': {
            'total_messages': db.get_message_count()
        },
        'timestamp': datetime.now().isoformat()
    }


@app.post("/api/test")
async def test_reply(
    request: TestMessageRequest,
    user: dict = Depends(require_login)
):
    """測試 AI 回覆"""
    # 清理輸入
    message = InputValidator.sanitize_string(request.message)

    # 檢查 SQL 注入
    if InputValidator.check_sql_injection(message):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='偵測到可疑輸入'
        )

    start_time = datetime.now()

    try:
        reply = generate_reply(message)
        processing_time = (datetime.now() - start_time).total_seconds() * 1000

        # 記錄到資料庫
        db = get_database()
        db.log_message(
            direction='test',
            user_id=user['username'],
            message=message,
            response=reply,
            processing_time_ms=int(processing_time)
        )

        return {
            'reply': reply,
            'processing_time_ms': processing_time
        }

    except Exception as e:
        logger.error(f"AI 回覆失敗: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='AI 服務暫時無法使用'
        )


@app.post("/api/refresh-cache")
async def refresh_cache(user: dict = Depends(require_login)):
    """刷新 Gemini 快取"""
    try:
        service = get_gemini_service()
        success = service.initialize_cache(force_refresh=True)

        # 記錄審計日誌
        db = get_database()
        db.log_audit(
            action='refresh_cache',
            user_id=user['user_id'],
            username=user['username'],
            resource='gemini_cache',
            details={'success': success}
        )

        return {
            'success': success,
            'message': '快取刷新成功' if success else '快取刷新失敗',
            'cache_info': service.get_cache_status()
        }
    except Exception as e:
        logger.error(f"刷新快取失敗: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@app.get("/api/messages")
async def get_messages(
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(require_login)
):
    """取得訊息記錄"""
    db = get_database()
    messages = db.get_messages(limit=limit, offset=offset)
    total = db.get_message_count()

    return {
        'messages': messages,
        'total': total,
        'limit': limit,
        'offset': offset
    }


# ============================================
# 管理員專用端點
# ============================================

@app.get("/api/audit-logs")
async def get_audit_logs(
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(require_admin)
):
    """取得審計日誌（僅管理員）"""
    db = get_database()
    logs = db.get_audit_logs(limit=limit, offset=offset)
    return {'logs': logs}


@app.post("/api/settings")
async def update_settings(
    settings: SettingsUpdate,
    user: dict = Depends(require_admin)
):
    """更新設定（僅管理員）"""
    db = get_database()

    # 儲存設定
    for key, value in settings.model_dump(exclude_none=True).items():
        db.set_setting(key, value, user['user_id'])

    # 記錄審計日誌
    db.log_audit(
        action='update_settings',
        user_id=user['user_id'],
        username=user['username'],
        resource='settings',
        details=settings.model_dump(exclude_none=True)
    )

    return {'success': True, 'message': '設定已更新'}


@app.post("/api/backup")
async def create_backup(user: dict = Depends(require_admin)):
    """建立資料庫備份（僅管理員）"""
    db = get_database()
    backup_file = db.backup()

    db.log_audit(
        action='create_backup',
        user_id=user['user_id'],
        username=user['username'],
        resource='database',
        details={'backup_file': backup_file}
    )

    return {'success': True, 'backup_file': backup_file}


# ============================================
# 輔助函數
# ============================================

def _get_user_permissions(role: str) -> list:
    """取得角色權限"""
    permissions = {
        'admin': ['read', 'write', 'delete', 'admin', 'audit', 'backup'],
        'manager': ['read', 'write', 'delete'],
        'user': ['read', 'write'],
        'guest': ['read']
    }
    return permissions.get(role, [])


# ============================================
# HTML 模板
# ============================================

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>登入 - 蝦皮 AI 客服</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .gradient-bg { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
    </style>
</head>
<body class="gradient-bg min-h-screen flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md">
        <div class="text-center mb-8">
            <div class="w-20 h-20 bg-gradient-to-r from-orange-500 to-red-500 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <svg class="w-10 h-10 text-white" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6z"/>
                </svg>
            </div>
            <h1 class="text-2xl font-bold text-gray-800">蝦皮 AI 客服系統</h1>
            <p class="text-gray-500 mt-2">請登入以繼續</p>
        </div>

        <form id="loginForm" class="space-y-6">
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-2">帳號</label>
                <input type="text" id="username" required
                    class="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-orange-500 focus:border-transparent transition"
                    placeholder="請輸入帳號">
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-2">密碼</label>
                <input type="password" id="password" required
                    class="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-orange-500 focus:border-transparent transition"
                    placeholder="請輸入密碼">
            </div>

            <div id="error" class="hidden bg-red-50 text-red-600 p-3 rounded-xl text-sm"></div>

            <button type="submit"
                class="w-full bg-gradient-to-r from-orange-500 to-red-500 text-white py-3 rounded-xl font-bold hover:opacity-90 transition">
                登入
            </button>
        </form>

        <p class="text-center text-gray-400 text-sm mt-6">
            預設帳號: admin / Admin@123456
        </p>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();

            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            const errorDiv = document.getElementById('error');

            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });

                const data = await res.json();

                if (res.ok) {
                    window.location.href = '/dashboard';
                } else {
                    errorDiv.textContent = data.detail || '登入失敗';
                    errorDiv.classList.remove('hidden');
                }
            } catch (err) {
                errorDiv.textContent = '網路錯誤，請稍後再試';
                errorDiv.classList.remove('hidden');
            }
        });
    </script>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>控制台 - 蝦皮 AI 客服</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .gradient-bg { background: linear-gradient(135deg, #ee4d2d 0%, #ff6b4a 100%); }
    </style>
</head>
<body class="bg-gray-100 min-h-screen">
    <!-- 頂部導覽 -->
    <nav class="gradient-bg text-white shadow-lg">
        <div class="max-w-6xl mx-auto px-4 py-4 flex justify-between items-center">
            <h1 class="text-xl font-bold">🛒 蝦皮 AI 客服控制台</h1>
            <div class="flex items-center gap-4">
                <span id="username" class="text-sm opacity-90"></span>
                <button onclick="logout()" class="bg-white/20 px-4 py-2 rounded-lg hover:bg-white/30 transition">
                    登出
                </button>
            </div>
        </div>
    </nav>

    <main class="max-w-6xl mx-auto p-4 mt-6">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <!-- 系統狀態 -->
            <div class="bg-white rounded-2xl shadow-lg p-6">
                <h2 class="text-lg font-bold text-gray-800 mb-4">📊 系統狀態</h2>
                <div id="status-content" class="space-y-3">
                    <p class="text-gray-500">載入中...</p>
                </div>
                <button onclick="refreshCache()" class="mt-4 w-full bg-blue-500 text-white py-2 rounded-xl hover:bg-blue-600 transition">
                    🔄 刷新知識庫快取
                </button>
            </div>

            <!-- 測試 AI -->
            <div class="bg-white rounded-2xl shadow-lg p-6">
                <h2 class="text-lg font-bold text-gray-800 mb-4">🤖 測試 AI 回覆</h2>
                <textarea id="test-message" rows="3"
                    class="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-orange-500 resize-none"
                    placeholder="輸入測試訊息..."></textarea>
                <button onclick="testReply()" class="mt-3 w-full bg-orange-500 text-white py-2 rounded-xl hover:bg-orange-600 transition">
                    發送測試
                </button>
                <div id="test-response" class="mt-4 p-4 bg-gray-50 rounded-xl text-sm hidden"></div>
            </div>

            <!-- 最近訊息 -->
            <div class="bg-white rounded-2xl shadow-lg p-6 md:col-span-2">
                <h2 class="text-lg font-bold text-gray-800 mb-4">📝 最近訊息記錄</h2>
                <div id="messages-content" class="space-y-2 max-h-80 overflow-y-auto">
                    <p class="text-gray-500">載入中...</p>
                </div>
            </div>
        </div>
    </main>

    <script>
        // 載入使用者資訊
        async function loadUser() {
            try {
                const res = await fetch('/api/me');
                if (res.status === 401) {
                    window.location.href = '/login';
                    return;
                }
                const data = await res.json();
                document.getElementById('username').textContent = data.user.username + ' (' + data.user.role + ')';
            } catch (e) {
                console.error(e);
            }
        }

        // 載入狀態
        async function loadStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();

                document.getElementById('status-content').innerHTML = `
                    <div class="flex justify-between py-2 border-b">
                        <span class="text-gray-600">Gemini API</span>
                        <span class="${data.gemini.api_key_set ? 'text-green-600' : 'text-red-600'} font-medium">
                            ${data.gemini.api_key_display}
                        </span>
                    </div>
                    <div class="flex justify-between py-2 border-b">
                        <span class="text-gray-600">總訊息數</span>
                        <span class="font-medium">${data.stats.total_messages}</span>
                    </div>
                    <div class="flex justify-between py-2">
                        <span class="text-gray-600">系統狀態</span>
                        <span class="text-green-600 font-medium">運行中</span>
                    </div>
                `;
            } catch (e) {
                console.error(e);
            }
        }

        // 載入訊息
        async function loadMessages() {
            try {
                const res = await fetch('/api/messages?limit=10');
                const data = await res.json();

                if (data.messages.length === 0) {
                    document.getElementById('messages-content').innerHTML = '<p class="text-gray-500">暫無訊息</p>';
                    return;
                }

                document.getElementById('messages-content').innerHTML = data.messages.map(msg => `
                    <div class="p-3 bg-gray-50 rounded-lg text-sm">
                        <div class="flex justify-between text-gray-500 text-xs mb-1">
                            <span>${msg.direction === 'incoming' ? '← 收到' : '→ 發送'}</span>
                            <span>${new Date(msg.created_at).toLocaleString()}</span>
                        </div>
                        <p class="text-gray-800">${msg.message}</p>
                        ${msg.response ? `<p class="text-blue-600 mt-1">↳ ${msg.response}</p>` : ''}
                    </div>
                `).join('');
            } catch (e) {
                console.error(e);
            }
        }

        // 測試回覆
        async function testReply() {
            const message = document.getElementById('test-message').value;
            if (!message) return;

            const responseDiv = document.getElementById('test-response');
            responseDiv.classList.remove('hidden');
            responseDiv.textContent = '思考中...';

            try {
                const res = await fetch('/api/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message })
                });
                const data = await res.json();

                responseDiv.textContent = data.reply || data.detail || '錯誤';
                loadMessages();
            } catch (e) {
                responseDiv.textContent = '錯誤: ' + e.message;
            }
        }

        // 刷新快取
        async function refreshCache() {
            try {
                const res = await fetch('/api/refresh-cache', { method: 'POST' });
                const data = await res.json();
                alert(data.message);
            } catch (e) {
                alert('刷新失敗');
            }
        }

        // 登出
        async function logout() {
            await fetch('/api/logout', { method: 'POST' });
            window.location.href = '/login';
        }

        // 初始化
        loadUser();
        loadStatus();
        loadMessages();
        setInterval(loadStatus, 30000);
        setInterval(loadMessages, 60000);
    </script>
</body>
</html>
"""


# ============================================
# 主程式
# ============================================

if __name__ == "__main__":
    port = int(os.getenv('PORT', '8000'))

    print(f"""
╔══════════════════════════════════════════════════════╗
║     蝦皮 AI 客服系統 - 安全強化版                      ║
╠══════════════════════════════════════════════════════╣
║  資安功能:                                            ║
║  ✓ 使用者認證（密碼雜湊儲存）                          ║
║  ✓ Session 管理（帶過期機制）                          ║
║  ✓ API 速率限制（防暴力破解）                          ║
║  ✓ 審計日誌（追蹤所有操作）                            ║
║  ✓ SQLite 持久化儲存                                  ║
║  ✓ 安全 HTTP 標頭                                     ║
║  ✓ 輸入驗證與清理                                     ║
╠══════════════════════════════════════════════════════╣
║  預設管理員: admin / Admin@123456                      ║
║  請立即登入並更改密碼！                                 ║
╚══════════════════════════════════════════════════════╝

🚀 啟動於 http://localhost:{port}
""")

    uvicorn.run(
        "server_secure:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )
