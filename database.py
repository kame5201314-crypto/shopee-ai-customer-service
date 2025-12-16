#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資料庫模組 - 使用 SQLite 持久化儲存

功能：
1. 使用者管理（帶密碼雜湊）
2. 設定儲存
3. 訊息記錄
4. 審計日誌
5. 自動備份機制
"""

import os
import json
import sqlite3
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from security import PasswordSecurity

logger = logging.getLogger(__name__)

# 資料庫檔案路徑
DB_DIR = Path(os.getenv('DB_DIR', './data'))
DB_FILE = DB_DIR / 'shopee_ai.db'
BACKUP_DIR = DB_DIR / 'backups'


class Database:
    """
    SQLite 資料庫管理類
    使用連接池和線程安全設計
    """

    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self.lock = Lock()

        # 確保目錄存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 初始化資料庫
        self._init_database()

    @contextmanager
    def get_connection(self):
        """取得資料庫連接（Context Manager）"""
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_database(self):
        """初始化資料庫表結構"""
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # 使用者表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT DEFAULT 'user',
                        email TEXT,
                        is_active BOOLEAN DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMP,
                        failed_attempts INTEGER DEFAULT 0,
                        locked_until TIMESTAMP
                    )
                ''')

                # 設定表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key TEXT UNIQUE NOT NULL,
                        value TEXT,
                        encrypted BOOLEAN DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_by INTEGER,
                        FOREIGN KEY (updated_by) REFERENCES users(id)
                    )
                ''')

                # 訊息記錄表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        direction TEXT NOT NULL,
                        user_id TEXT,
                        message TEXT NOT NULL,
                        message_type TEXT DEFAULT 'text',
                        response TEXT,
                        response_type TEXT,
                        processing_time_ms INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # 審計日誌表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        user_id INTEGER,
                        username TEXT,
                        action TEXT NOT NULL,
                        resource TEXT,
                        resource_id TEXT,
                        ip_address TEXT,
                        user_agent TEXT,
                        request_path TEXT,
                        request_method TEXT,
                        status_code INTEGER,
                        details TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(id)
                    )
                ''')

                # Session 表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        ip_address TEXT,
                        user_agent TEXT,
                        is_valid BOOLEAN DEFAULT 1,
                        FOREIGN KEY (user_id) REFERENCES users(id)
                    )
                ''')

                # 建立索引
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)')

                logger.info("資料庫初始化完成")

    # ============================================
    # 使用者管理
    # ============================================

    def create_user(self, username: str, password: str, role: str = 'user', email: str = None) -> int:
        """建立新使用者"""
        # 驗證密碼強度
        valid, msg = PasswordSecurity.validate_password_strength(password)
        if not valid:
            raise ValueError(msg)

        password_hash = PasswordSecurity.hash_password(password)

        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute('''
                        INSERT INTO users (username, password_hash, role, email)
                        VALUES (?, ?, ?, ?)
                    ''', (username, password_hash, role, email))
                    user_id = cursor.lastrowid
                    logger.info(f"使用者已建立: {username} (ID: {user_id})")
                    return user_id
                except sqlite3.IntegrityError:
                    raise ValueError(f"使用者名稱已存在: {username}")

    def verify_user(self, username: str, password: str) -> Optional[Dict]:
        """驗證使用者登入"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, role, is_active,
                       failed_attempts, locked_until
                FROM users WHERE username = ?
            ''', (username,))
            row = cursor.fetchone()

            if not row:
                return None

            user = dict(row)

            # 檢查帳戶是否被鎖定
            if user['locked_until']:
                locked_until = datetime.fromisoformat(user['locked_until'])
                if datetime.now() < locked_until:
                    return {'error': 'locked', 'until': user['locked_until']}

            # 檢查帳戶是否啟用
            if not user['is_active']:
                return {'error': 'disabled'}

            # 驗證密碼
            if PasswordSecurity.verify_password(password, user['password_hash']):
                # 登入成功，重置失敗次數
                cursor.execute('''
                    UPDATE users
                    SET failed_attempts = 0, last_login = ?, locked_until = NULL
                    WHERE id = ?
                ''', (datetime.now().isoformat(), user['id']))
                conn.commit()

                return {
                    'id': user['id'],
                    'username': user['username'],
                    'role': user['role']
                }
            else:
                # 登入失敗，增加失敗次數
                new_attempts = user['failed_attempts'] + 1
                locked_until = None

                if new_attempts >= 5:
                    locked_until = (datetime.now() + timedelta(minutes=15)).isoformat()

                cursor.execute('''
                    UPDATE users
                    SET failed_attempts = ?, locked_until = ?
                    WHERE id = ?
                ''', (new_attempts, locked_until, user['id']))
                conn.commit()

                return {'error': 'invalid_password', 'attempts': new_attempts}

    def get_user(self, user_id: int) -> Optional[Dict]:
        """取得使用者資訊"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, role, email, is_active, created_at, last_login
                FROM users WHERE id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_password(self, user_id: int, new_password: str):
        """更新密碼"""
        valid, msg = PasswordSecurity.validate_password_strength(new_password)
        if not valid:
            raise ValueError(msg)

        password_hash = PasswordSecurity.hash_password(new_password)

        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users
                    SET password_hash = ?, updated_at = ?
                    WHERE id = ?
                ''', (password_hash, datetime.now().isoformat(), user_id))

    # ============================================
    # 設定管理
    # ============================================

    def get_setting(self, key: str, default: Any = None) -> Any:
        """取得設定值"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cursor.fetchone()

            if row:
                try:
                    return json.loads(row['value'])
                except (json.JSONDecodeError, TypeError):
                    return row['value']

            return default

    def set_setting(self, key: str, value: Any, user_id: int = None):
        """儲存設定值"""
        value_str = json.dumps(value) if not isinstance(value, str) else value

        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO settings (key, value, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                ''', (key, value_str, datetime.now().isoformat(), user_id))

    def get_all_settings(self) -> Dict[str, Any]:
        """取得所有設定"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key, value FROM settings')
            rows = cursor.fetchall()

            settings = {}
            for row in rows:
                try:
                    settings[row['key']] = json.loads(row['value'])
                except (json.JSONDecodeError, TypeError):
                    settings[row['key']] = row['value']

            return settings

    # ============================================
    # 訊息記錄
    # ============================================

    def log_message(
        self,
        direction: str,
        user_id: str,
        message: str,
        message_type: str = 'text',
        response: str = None,
        response_type: str = None,
        processing_time_ms: int = None
    ):
        """記錄訊息"""
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO messages
                    (direction, user_id, message, message_type, response,
                     response_type, processing_time_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (direction, user_id, message, message_type, response,
                      response_type, processing_time_ms))

    def get_messages(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """取得訊息記錄"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM messages
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            ''', (limit, offset))
            return [dict(row) for row in cursor.fetchall()]

    def get_message_count(self) -> int:
        """取得訊息總數"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM messages')
            return cursor.fetchone()['count']

    # ============================================
    # 審計日誌
    # ============================================

    def log_audit(
        self,
        action: str,
        user_id: int = None,
        username: str = None,
        resource: str = None,
        resource_id: str = None,
        ip_address: str = None,
        user_agent: str = None,
        request_path: str = None,
        request_method: str = None,
        status_code: int = None,
        details: Dict = None
    ):
        """記錄審計日誌"""
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO audit_logs
                    (user_id, username, action, resource, resource_id,
                     ip_address, user_agent, request_path, request_method,
                     status_code, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, username, action, resource, resource_id,
                    ip_address, user_agent, request_path, request_method,
                    status_code, json.dumps(details) if details else None
                ))

    def get_audit_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        user_id: int = None,
        action: str = None,
        start_date: datetime = None,
        end_date: datetime = None
    ) -> List[Dict]:
        """取得審計日誌"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            query = 'SELECT * FROM audit_logs WHERE 1=1'
            params = []

            if user_id:
                query += ' AND user_id = ?'
                params.append(user_id)

            if action:
                query += ' AND action = ?'
                params.append(action)

            if start_date:
                query += ' AND timestamp >= ?'
                params.append(start_date.isoformat())

            if end_date:
                query += ' AND timestamp <= ?'
                params.append(end_date.isoformat())

            query += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # ============================================
    # 備份與維護
    # ============================================

    def backup(self) -> str:
        """建立資料庫備份"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = BACKUP_DIR / f'backup_{timestamp}.db'

        with self.lock:
            shutil.copy2(self.db_path, backup_file)
            logger.info(f"資料庫備份完成: {backup_file}")

        # 清理舊備份（保留最近 7 天）
        self._cleanup_old_backups()

        return str(backup_file)

    def _cleanup_old_backups(self, days: int = 7):
        """清理舊備份"""
        cutoff = datetime.now() - timedelta(days=days)

        for backup_file in BACKUP_DIR.glob('backup_*.db'):
            try:
                # 從檔名解析時間
                timestamp_str = backup_file.stem.replace('backup_', '')
                file_time = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')

                if file_time < cutoff:
                    backup_file.unlink()
                    logger.info(f"已刪除舊備份: {backup_file}")
            except (ValueError, OSError):
                continue

    def cleanup_old_data(self, days: int = 30):
        """清理舊資料"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # 清理舊訊息
                cursor.execute(
                    'DELETE FROM messages WHERE created_at < ?',
                    (cutoff,)
                )
                messages_deleted = cursor.rowcount

                # 清理舊審計日誌
                cursor.execute(
                    'DELETE FROM audit_logs WHERE timestamp < ?',
                    (cutoff,)
                )
                logs_deleted = cursor.rowcount

                # 清理無效 Session
                cursor.execute(
                    'DELETE FROM sessions WHERE is_valid = 0 OR last_activity < ?',
                    (cutoff,)
                )
                sessions_deleted = cursor.rowcount

                logger.info(
                    f"資料清理完成: 訊息={messages_deleted}, "
                    f"審計日誌={logs_deleted}, Session={sessions_deleted}"
                )


# 全域資料庫實例
_db = None
_db_lock = Lock()


def get_database() -> Database:
    """取得資料庫單例"""
    global _db

    with _db_lock:
        if _db is None:
            _db = Database()

        return _db


def init_default_admin():
    """初始化預設管理員帳號（如果不存在）"""
    db = get_database()

    # 檢查是否已有管理員
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
        if cursor.fetchone():
            return  # 已有管理員，不需要建立

    # 建立預設管理員
    default_password = os.getenv('DEFAULT_ADMIN_PASSWORD', 'Admin@123456')

    try:
        db.create_user(
            username='admin',
            password=default_password,
            role='admin',
            email='admin@localhost'
        )
        logger.warning(
            "已建立預設管理員帳號 (admin)。"
            "請立即登入並更改密碼！"
        )
    except ValueError as e:
        logger.error(f"無法建立預設管理員: {e}")
