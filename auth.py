#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
認證與授權模組

功能：
1. 使用者登入/登出
2. Session 管理
3. JWT Token 生成與驗證（可選）
4. 權限檢查中間件
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
from functools import wraps

from fastapi import Request, Response, HTTPException, status, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import RedirectResponse, JSONResponse

from database import get_database
from security import (
    PasswordSecurity,
    rate_limiter,
    InputValidator,
    get_security_headers
)

logger = logging.getLogger(__name__)

# HTTP Basic 認證
security = HTTPBasic(auto_error=False)


class AuthService:
    """認證服務"""

    def __init__(self):
        self.db = get_database()

    def login(
        self,
        username: str,
        password: str,
        ip_address: str = None,
        user_agent: str = None
    ) -> Dict:
        """
        使用者登入

        返回:
        - 成功: {'success': True, 'session_id': ..., 'user': ...}
        - 失敗: {'success': False, 'error': ..., 'message': ...}
        """
        # 清理輸入
        username = InputValidator.sanitize_string(username, 50)

        # 驗證使用者
        result = self.db.verify_user(username, password)

        if result is None:
            # 使用者不存在
            self._log_auth_event('login_failed', username, ip_address, '使用者不存在')
            return {
                'success': False,
                'error': 'invalid_credentials',
                'message': '帳號或密碼錯誤'
            }

        if 'error' in result:
            error = result['error']

            if error == 'locked':
                self._log_auth_event('login_blocked', username, ip_address, '帳戶被鎖定')
                return {
                    'success': False,
                    'error': 'locked',
                    'message': f"帳戶已被鎖定，請於 {result['until']} 後重試"
                }

            if error == 'disabled':
                self._log_auth_event('login_failed', username, ip_address, '帳戶已停用')
                return {
                    'success': False,
                    'error': 'disabled',
                    'message': '此帳戶已被停用'
                }

            if error == 'invalid_password':
                self._log_auth_event('login_failed', username, ip_address, f"密碼錯誤 ({result['attempts']} 次)")
                remaining = 5 - result['attempts']
                return {
                    'success': False,
                    'error': 'invalid_credentials',
                    'message': f"帳號或密碼錯誤，剩餘 {remaining} 次嘗試機會"
                }

        # 登入成功，建立 Session
        session_id = PasswordSecurity.generate_secure_token(32)

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO sessions (id, user_id, ip_address, user_agent)
                VALUES (?, ?, ?, ?)
            ''', (session_id, result['id'], ip_address, user_agent))

        self._log_auth_event('login_success', username, ip_address)

        return {
            'success': True,
            'session_id': session_id,
            'user': {
                'id': result['id'],
                'username': result['username'],
                'role': result['role']
            }
        }

    def logout(self, session_id: str, ip_address: str = None) -> bool:
        """使用者登出"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            # 取得 Session 資訊
            cursor.execute(
                'SELECT user_id FROM sessions WHERE id = ?',
                (session_id,)
            )
            row = cursor.fetchone()

            if row:
                # 標記 Session 無效
                cursor.execute(
                    'UPDATE sessions SET is_valid = 0 WHERE id = ?',
                    (session_id,)
                )

                # 記錄登出事件
                user = self.db.get_user(row['user_id'])
                if user:
                    self._log_auth_event('logout', user['username'], ip_address)

                return True

        return False

    def validate_session(self, session_id: str) -> Optional[Dict]:
        """
        驗證 Session

        返回使用者資訊，或 None（如果無效）
        """
        if not session_id:
            return None

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.*, u.username, u.role
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.id = ? AND s.is_valid = 1
            ''', (session_id,))
            row = cursor.fetchone()

            if not row:
                return None

            session = dict(row)

            # 檢查是否過期（60 分鐘）
            last_activity = datetime.fromisoformat(session['last_activity'])
            if datetime.now() - last_activity > timedelta(minutes=60):
                # Session 過期
                cursor.execute(
                    'UPDATE sessions SET is_valid = 0 WHERE id = ?',
                    (session_id,)
                )
                return None

            # 更新最後活動時間
            cursor.execute(
                'UPDATE sessions SET last_activity = ? WHERE id = ?',
                (datetime.now().isoformat(), session_id)
            )
            conn.commit()

            return {
                'user_id': session['user_id'],
                'username': session['username'],
                'role': session['role']
            }

    def change_password(
        self,
        user_id: int,
        old_password: str,
        new_password: str
    ) -> Dict:
        """變更密碼"""
        # 驗證舊密碼
        user = self.db.get_user(user_id)
        if not user:
            return {'success': False, 'message': '使用者不存在'}

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT password_hash FROM users WHERE id = ?',
                (user_id,)
            )
            row = cursor.fetchone()

            if not PasswordSecurity.verify_password(old_password, row['password_hash']):
                return {'success': False, 'message': '舊密碼不正確'}

        # 更新密碼
        try:
            self.db.update_password(user_id, new_password)
            self._log_auth_event('password_changed', user['username'])
            return {'success': True, 'message': '密碼已更新'}
        except ValueError as e:
            return {'success': False, 'message': str(e)}

    def _log_auth_event(
        self,
        action: str,
        username: str,
        ip_address: str = None,
        details: str = None
    ):
        """記錄認證事件"""
        self.db.log_audit(
            action=action,
            username=username,
            resource='auth',
            ip_address=ip_address,
            details={'message': details} if details else None
        )


# 全域認證服務
_auth_service = None


def get_auth_service() -> AuthService:
    """取得認證服務單例"""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


# ============================================
# FastAPI 依賴項
# ============================================

async def get_current_user(request: Request) -> Optional[Dict]:
    """
    取得當前登入使用者

    用法：
    @app.get("/api/protected")
    async def protected_route(user: Dict = Depends(get_current_user)):
        if not user:
            raise HTTPException(status_code=401)
        return {"user": user}
    """
    session_id = request.cookies.get('session_id')
    if not session_id:
        session_id = request.headers.get('X-Session-ID')

    if not session_id:
        return None

    auth_service = get_auth_service()
    return auth_service.validate_session(session_id)


async def require_login(user: Optional[Dict] = Depends(get_current_user)) -> Dict:
    """
    要求使用者登入

    用法：
    @app.get("/api/protected")
    async def protected_route(user: Dict = Depends(require_login)):
        return {"user": user}
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='請先登入'
        )
    return user


async def require_admin(user: Dict = Depends(require_login)) -> Dict:
    """
    要求管理員權限

    用法：
    @app.get("/api/admin")
    async def admin_route(user: Dict = Depends(require_admin)):
        return {"user": user}
    """
    if user.get('role') != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='需要管理員權限'
        )
    return user


def require_role(allowed_roles: list):
    """
    要求特定角色

    用法：
    @app.get("/api/manager")
    async def manager_route(user: Dict = Depends(require_role(['admin', 'manager']))):
        return {"user": user}
    """
    async def role_checker(user: Dict = Depends(require_login)) -> Dict:
        if user.get('role') not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {', '.join(allowed_roles)}"
            )
        return user

    return role_checker


# ============================================
# 認證中間件
# ============================================

class AuthMiddleware:
    """認證中間件"""

    # 不需要認證的路徑
    PUBLIC_PATHS = [
        '/',
        '/login',
        '/api/login',
        '/api/health',
        '/static',
        '/favicon.ico',
    ]

    # 不需要認證的路徑前綴
    PUBLIC_PREFIXES = [
        '/static/',
        '/assets/',
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        # 檢查是否為公開路徑
        if self._is_public_path(path):
            await self.app(scope, receive, send)
            return

        # 檢查認證
        session_id = request.cookies.get('session_id')
        if not session_id:
            session_id = request.headers.get('X-Session-ID')

        if session_id:
            auth_service = get_auth_service()
            user = auth_service.validate_session(session_id)
            if user:
                # 認證成功，將使用者資訊加入 scope
                scope['user'] = user
                await self.app(scope, receive, send)
                return

        # 認證失敗
        if path.startswith('/api/'):
            # API 請求返回 JSON
            response = JSONResponse(
                status_code=401,
                content={'error': '需要登入', 'redirect': '/login'}
            )
        else:
            # 頁面請求重定向到登入頁
            response = RedirectResponse(url='/login', status_code=302)

        await response(scope, receive, send)

    def _is_public_path(self, path: str) -> bool:
        """檢查是否為公開路徑"""
        if path in self.PUBLIC_PATHS:
            return True

        for prefix in self.PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return True

        return False


# ============================================
# 安全標頭中間件
# ============================================

class SecurityHeadersMiddleware:
    """安全標頭中間件"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message['type'] == 'http.response.start':
                headers = dict(message.get('headers', []))

                # 添加安全標頭
                for key, value in get_security_headers().items():
                    headers[key.lower().encode()] = value.encode()

                message['headers'] = list(headers.items())

            await send(message)

        await self.app(scope, receive, send_wrapper)


# ============================================
# 速率限制中間件
# ============================================

class RateLimitMiddleware:
    """速率限制中間件"""

    # 需要嚴格限制的路徑
    STRICT_PATHS = ['/api/login', '/login']

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        # 檢查速率限制
        if not rate_limiter.check_rate_limit(request):
            response = JSONResponse(
                status_code=429,
                content={
                    'error': '請求過於頻繁',
                    'message': '請稍後再試',
                    'retry_after': 60
                }
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# ============================================
# 審計中間件
# ============================================

class AuditMiddleware:
    """審計中間件 - 記錄所有請求"""

    # 需要記錄的路徑前綴
    AUDIT_PREFIXES = ['/api/']

    # 不需要記錄的路徑
    SKIP_PATHS = ['/api/health', '/api/status']

    def __init__(self, app):
        self.app = app
        self.db = get_database()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        # 檢查是否需要審計
        if not self._should_audit(path):
            await self.app(scope, receive, send)
            return

        # 記錄請求資訊
        start_time = datetime.now()
        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code
            if message['type'] == 'http.response.start':
                status_code = message['status']
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # 記錄審計日誌
            user = scope.get('user', {})
            self.db.log_audit(
                action='api_request',
                user_id=user.get('user_id'),
                username=user.get('username'),
                resource='api',
                resource_id=path,
                ip_address=self._get_client_ip(request),
                user_agent=request.headers.get('user-agent'),
                request_path=path,
                request_method=request.method,
                status_code=status_code
            )

    def _should_audit(self, path: str) -> bool:
        """檢查是否需要審計"""
        if path in self.SKIP_PATHS:
            return False

        for prefix in self.AUDIT_PREFIXES:
            if path.startswith(prefix):
                return True

        return False

    def _get_client_ip(self, request: Request) -> str:
        """取得客戶端 IP"""
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.client.host if request.client else 'unknown'
