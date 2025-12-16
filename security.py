#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資安模組 - 核心安全功能

功能：
1. API Key 安全管理（永不暴露到前端）
2. 請求速率限制（防暴力破解）
3. 輸入驗證與清理
4. 安全標頭設定
5. IP 黑名單管理
"""

import os
import re
import time
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
from threading import Lock
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ============================================
# 速率限制器 (Rate Limiter)
# ============================================

class RateLimiter:
    """
    滑動窗口速率限制器
    防止暴力破解和 API 濫用
    """

    def __init__(self):
        self.requests: Dict[str, list] = defaultdict(list)
        self.blocked_ips: Dict[str, datetime] = {}
        self.failed_attempts: Dict[str, int] = defaultdict(int)
        self.lock = Lock()

        # 設定
        self.rate_limit = int(os.getenv('RATE_LIMIT_PER_MINUTE', '60'))
        self.block_duration = int(os.getenv('BLOCK_DURATION_MINUTES', '15'))
        self.max_failed_attempts = int(os.getenv('MAX_FAILED_ATTEMPTS', '5'))

    def _get_client_ip(self, request: Request) -> str:
        """取得客戶端 IP"""
        # 處理代理情況
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.client.host if request.client else 'unknown'

    def _clean_old_requests(self, ip: str, window: int = 60):
        """清理過期的請求記錄"""
        current_time = time.time()
        self.requests[ip] = [
            t for t in self.requests[ip]
            if current_time - t < window
        ]

    def is_blocked(self, ip: str) -> bool:
        """檢查 IP 是否被封鎖"""
        if ip in self.blocked_ips:
            if datetime.now() < self.blocked_ips[ip]:
                return True
            else:
                # 解除封鎖
                del self.blocked_ips[ip]
                self.failed_attempts[ip] = 0
        return False

    def block_ip(self, ip: str, reason: str = ""):
        """封鎖 IP"""
        with self.lock:
            block_until = datetime.now() + timedelta(minutes=self.block_duration)
            self.blocked_ips[ip] = block_until
            logger.warning(f"IP 已封鎖: {ip}, 原因: {reason}, 直到: {block_until}")

    def record_failed_attempt(self, ip: str) -> bool:
        """記錄失敗嘗試，返回是否達到上限"""
        with self.lock:
            self.failed_attempts[ip] += 1
            if self.failed_attempts[ip] >= self.max_failed_attempts:
                self.block_ip(ip, f"連續失敗 {self.failed_attempts[ip]} 次")
                return True
        return False

    def reset_failed_attempts(self, ip: str):
        """重置失敗嘗試次數"""
        with self.lock:
            self.failed_attempts[ip] = 0

    def check_rate_limit(self, request: Request) -> bool:
        """
        檢查請求是否超過速率限制
        返回 True 表示允許，False 表示拒絕
        """
        ip = self._get_client_ip(request)

        with self.lock:
            # 檢查是否被封鎖
            if self.is_blocked(ip):
                return False

            # 清理舊請求
            self._clean_old_requests(ip)

            # 檢查速率限制
            if len(self.requests[ip]) >= self.rate_limit:
                logger.warning(f"速率限制觸發: {ip}, 請求數: {len(self.requests[ip])}")
                return False

            # 記錄此次請求
            self.requests[ip].append(time.time())
            return True

    def get_status(self, request: Request) -> dict:
        """取得速率限制狀態"""
        ip = self._get_client_ip(request)
        self._clean_old_requests(ip)

        return {
            'ip': ip,
            'requests_in_window': len(self.requests.get(ip, [])),
            'limit': self.rate_limit,
            'is_blocked': self.is_blocked(ip),
            'failed_attempts': self.failed_attempts.get(ip, 0)
        }


# 全域速率限制器
rate_limiter = RateLimiter()


def rate_limit_middleware(request: Request):
    """速率限制中間件"""
    if not rate_limiter.check_rate_limit(request):
        ip = rate_limiter._get_client_ip(request)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                'error': '請求過於頻繁，請稍後再試',
                'retry_after': 60
            }
        )


# ============================================
# 輸入驗證與清理
# ============================================

class InputValidator:
    """輸入驗證器"""

    # 危險字元和模式
    DANGEROUS_PATTERNS = [
        r'<script[^>]*>.*?</script>',  # XSS
        r'javascript:',                  # JS 注入
        r'on\w+\s*=',                   # 事件處理器
        r'<!--.*?-->',                  # HTML 註解
        r'<iframe[^>]*>',               # iframe
        r'eval\s*\(',                   # eval
        r'exec\s*\(',                   # exec
        r'__import__',                  # Python import
    ]

    # SQL 注入關鍵字
    SQL_KEYWORDS = [
        'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'DROP',
        'UNION', 'OR 1=1', 'AND 1=1', '--', ';--', '/*'
    ]

    @classmethod
    def sanitize_string(cls, value: str, max_length: int = 10000) -> str:
        """清理字串輸入"""
        if not isinstance(value, str):
            value = str(value)

        # 限制長度
        value = value[:max_length]

        # 移除危險模式
        for pattern in cls.DANGEROUS_PATTERNS:
            value = re.sub(pattern, '', value, flags=re.IGNORECASE | re.DOTALL)

        # HTML 實體編碼
        value = (
            value
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#x27;')
        )

        return value.strip()

    @classmethod
    def validate_api_key_format(cls, key: str) -> bool:
        """驗證 API Key 格式（不驗證有效性）"""
        if not key or not isinstance(key, str):
            return False

        # Gemini API Key 格式: AIza...
        if key.startswith('AIza') and len(key) >= 30:
            return True

        # OpenAI API Key 格式: sk-...
        if key.startswith('sk-') and len(key) >= 40:
            return True

        return False

    @classmethod
    def check_sql_injection(cls, value: str) -> bool:
        """檢查是否有 SQL 注入嘗試"""
        upper_value = value.upper()
        for keyword in cls.SQL_KEYWORDS:
            if keyword in upper_value:
                return True
        return False

    @classmethod
    def validate_filename(cls, filename: str) -> bool:
        """驗證檔案名稱安全性"""
        # 不允許路徑遍歷
        if '..' in filename or '/' in filename or '\\' in filename:
            return False

        # 只允許特定副檔名
        allowed_extensions = ['.csv', '.txt', '.json', '.xlsx']
        ext = os.path.splitext(filename)[1].lower()
        if ext not in allowed_extensions:
            return False

        # 檔名只能包含安全字元
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', filename):
            return False

        return True


# ============================================
# 密碼安全
# ============================================

class PasswordSecurity:
    """密碼安全處理"""

    # 使用 PBKDF2 進行密碼雜湊（不需要額外安裝套件）
    ITERATIONS = 100000

    @classmethod
    def hash_password(cls, password: str) -> str:
        """
        雜湊密碼（使用 PBKDF2-SHA256）
        返回格式: salt:hash
        """
        salt = secrets.token_hex(32)
        key = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            cls.ITERATIONS
        )
        return f"{salt}:{key.hex()}"

    @classmethod
    def verify_password(cls, password: str, hashed: str) -> bool:
        """驗證密碼"""
        try:
            salt, stored_hash = hashed.split(':')
            key = hashlib.pbkdf2_hmac(
                'sha256',
                password.encode('utf-8'),
                salt.encode('utf-8'),
                cls.ITERATIONS
            )
            return key.hex() == stored_hash
        except Exception:
            return False

    @classmethod
    def validate_password_strength(cls, password: str) -> tuple[bool, str]:
        """
        驗證密碼強度
        返回: (是否通過, 錯誤訊息)
        """
        if len(password) < 8:
            return False, "密碼長度至少需要 8 個字元"

        if not re.search(r'[a-z]', password):
            return False, "密碼需包含小寫字母"

        if not re.search(r'[A-Z]', password):
            return False, "密碼需包含大寫字母"

        if not re.search(r'\d', password):
            return False, "密碼需包含數字"

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return False, "密碼需包含特殊字元"

        return True, "密碼強度符合要求"

    @classmethod
    def generate_secure_token(cls, length: int = 32) -> str:
        """生成安全的隨機 Token"""
        return secrets.token_urlsafe(length)


# ============================================
# Session 管理
# ============================================

class SessionManager:
    """Session 管理器"""

    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
        self.lock = Lock()
        self.session_timeout = int(os.getenv('SESSION_TIMEOUT_MINUTES', '60'))

    def create_session(self, user_id: str, user_data: dict = None) -> str:
        """建立新 Session"""
        with self.lock:
            session_id = PasswordSecurity.generate_secure_token(32)
            self.sessions[session_id] = {
                'user_id': user_id,
                'created_at': datetime.now(),
                'last_activity': datetime.now(),
                'data': user_data or {}
            }
            return session_id

    def validate_session(self, session_id: str) -> Optional[Dict]:
        """驗證 Session"""
        with self.lock:
            if session_id not in self.sessions:
                return None

            session = self.sessions[session_id]

            # 檢查是否過期
            elapsed = datetime.now() - session['last_activity']
            if elapsed > timedelta(minutes=self.session_timeout):
                del self.sessions[session_id]
                return None

            # 更新最後活動時間
            session['last_activity'] = datetime.now()
            return session

    def destroy_session(self, session_id: str):
        """銷毀 Session"""
        with self.lock:
            if session_id in self.sessions:
                del self.sessions[session_id]

    def cleanup_expired(self):
        """清理過期的 Sessions"""
        with self.lock:
            expired = []
            for sid, session in self.sessions.items():
                elapsed = datetime.now() - session['last_activity']
                if elapsed > timedelta(minutes=self.session_timeout):
                    expired.append(sid)

            for sid in expired:
                del self.sessions[sid]

            if expired:
                logger.info(f"已清理 {len(expired)} 個過期 Session")


# 全域 Session 管理器
session_manager = SessionManager()


# ============================================
# 安全標頭
# ============================================

def get_security_headers() -> dict:
    """取得安全 HTTP 標頭"""
    return {
        # 防止點擊劫持
        'X-Frame-Options': 'DENY',

        # 防止 MIME 類型嗅探
        'X-Content-Type-Options': 'nosniff',

        # XSS 保護
        'X-XSS-Protection': '1; mode=block',

        # 嚴格傳輸安全（HTTPS）
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',

        # 內容安全策略
        'Content-Security-Policy': (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://cdnjs.cloudflare.com; "
            "connect-src 'self';"
        ),

        # 引用來源政策
        'Referrer-Policy': 'strict-origin-when-cross-origin',

        # 權限政策
        'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
    }


# ============================================
# API Key 安全存取
# ============================================

class SecureKeyManager:
    """
    安全的 API Key 管理器
    確保敏感資料永不暴露到前端
    """

    def __init__(self):
        self._keys: Dict[str, str] = {}
        self.lock = Lock()

    def set_key(self, name: str, value: str):
        """安全儲存 API Key"""
        with self.lock:
            # 驗證格式
            if not InputValidator.validate_api_key_format(value):
                raise ValueError(f"無效的 API Key 格式: {name}")

            self._keys[name] = value
            logger.info(f"API Key 已設定: {name}")

    def get_key(self, name: str) -> Optional[str]:
        """取得 API Key（僅供後端使用）"""
        with self.lock:
            return self._keys.get(name)

    def get_masked_key(self, name: str) -> str:
        """取得遮蔽後的 API Key（可顯示在前端）"""
        with self.lock:
            key = self._keys.get(name)
            if not key:
                return "未設定"

            if len(key) <= 12:
                return "***已設定***"

            return f"{key[:6]}...{key[-4:]}"

    def has_key(self, name: str) -> bool:
        """檢查是否有設定 API Key"""
        with self.lock:
            return name in self._keys and bool(self._keys[name])

    def remove_key(self, name: str):
        """移除 API Key"""
        with self.lock:
            if name in self._keys:
                del self._keys[name]
                logger.info(f"API Key 已移除: {name}")


# 全域 Key 管理器
key_manager = SecureKeyManager()


# ============================================
# 裝飾器
# ============================================

def require_auth(func):
    """需要認證的裝飾器"""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        # 從 Cookie 或 Header 取得 Session
        session_id = request.cookies.get('session_id') or \
                     request.headers.get('X-Session-ID')

        if not session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='需要登入'
            )

        session = session_manager.validate_session(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Session 已過期，請重新登入'
            )

        # 將 session 資訊傳遞給處理函數
        request.state.session = session
        return await func(request, *args, **kwargs)

    return wrapper


def require_role(required_role: str):
    """需要特定角色的裝飾器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            session = getattr(request.state, 'session', None)
            if not session:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail='需要登入'
                )

            user_role = session.get('data', {}).get('role', 'user')

            # 角色層級
            role_hierarchy = {'admin': 3, 'manager': 2, 'user': 1, 'guest': 0}

            if role_hierarchy.get(user_role, 0) < role_hierarchy.get(required_role, 0):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail='權限不足'
                )

            return await func(request, *args, **kwargs)

        return wrapper
    return decorator


# ============================================
# 初始化
# ============================================

def init_security():
    """初始化安全模組"""
    # 從環境變數載入 API Keys
    gemini_key = os.getenv('GEMINI_API_KEY')
    if gemini_key:
        try:
            key_manager.set_key('gemini', gemini_key)
        except ValueError as e:
            logger.error(f"Gemini API Key 格式錯誤: {e}")

    logger.info("安全模組初始化完成")
