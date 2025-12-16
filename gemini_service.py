#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Context Caching 服務模組
使用 Gemini 2.5 Flash + Context Caching 實現低成本高精準度的客服回覆

更新：整合 knowledge_loader 支援多種檔案格式的知識庫
"""

import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import google.generativeai as genai
from google.generativeai import caching

# 導入知識庫載入器
from knowledge_loader import get_knowledge_loader, load_knowledge_base

logger = logging.getLogger(__name__)

class GeminiService:
    """
    Gemini Context Caching 服務

    特點：
    - 初始化時載入 products.csv 和 faq.txt 建立快取
    - 快取 TTL 24 小時，自動刷新
    - 大幅降低重複 token 成本（快取 token 便宜 75%）
    """

    def __init__(
        self,
        api_key: str,
        knowledge_folder: str = 'knowledge_base',
        products_file: str = 'products.csv',
        faq_file: str = 'faq.txt',
        model_name: str = 'gemini-2.0-flash',
        cache_ttl_hours: int = 24
    ):
        """
        初始化 Gemini 服務

        Args:
            api_key: Gemini API Key
            knowledge_folder: 知識庫資料夾路徑
            products_file: 產品資料檔案路徑（向後相容）
            faq_file: FAQ 檔案路徑（向後相容）
            model_name: 使用的模型名稱
            cache_ttl_hours: 快取存活時間（小時）
        """
        self.api_key = api_key
        self.knowledge_folder = knowledge_folder
        self.products_file = products_file
        self.faq_file = faq_file
        self.model_name = model_name
        self.cache_ttl_hours = cache_ttl_hours

        self.cached_content = None
        self.model = None
        self.cache_created_at = None
        self.lock = Lock()

        # 知識庫載入器
        self.knowledge_loader = get_knowledge_loader(knowledge_folder)

        # 設定 API Key
        genai.configure(api_key=api_key)

        # 系統指令（含防呆機制）
        self.system_instruction = """你是一位親切專業的蝦皮電商客服人員。

## 你的知識來源
你擁有讀取 knowledge_base 資料夾中文件的能力。以下是已載入的知識庫內容，請**只根據這些資料**回答客戶問題：

{knowledge_content}

## 回覆規則
1. **精準回答**：只根據上述資料回答，不要編造或猜測
2. **簡潔有禮**：回答不超過 100 字，使用繁體中文
3. **查無資料時**：如果在上述資料中找不到答案，請回覆：「感謝您的詢問！這個問題需要專人為您服務，請稍候，我將為您轉接客服人員。」
4. **不確定時**：寧可轉接人工，也不要給出錯誤資訊
5. **語氣親切**：像朋友一樣親切，但保持專業

## 禁止事項
- 禁止編造產品規格、價格、庫存等資訊
- 禁止承諾資料中沒有的優惠或服務
- 禁止回答與產品無關的問題（如政治、宗教等）"""

    def _load_file_content(self, filepath: str) -> str:
        """載入檔案內容"""
        try:
            path = Path(filepath)
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                logger.info(f"已載入檔案: {filepath} ({len(content)} 字元)")
                return content
            else:
                logger.warning(f"檔案不存在: {filepath}")
                return f"[{filepath} 檔案不存在]"
        except Exception as e:
            logger.error(f"載入檔案失敗 {filepath}: {e}")
            return f"[載入 {filepath} 失敗]"

    def _build_knowledge_content(self) -> str:
        """建構知識庫內容（優先使用 knowledge_base 資料夾）"""
        # 優先從 knowledge_base 資料夾載入
        knowledge_content = self.knowledge_loader.get_knowledge_content()

        # 如果知識庫為空，嘗試載入舊的 products.csv 和 faq.txt
        if not knowledge_content or "[知識庫為空" in knowledge_content:
            logger.info("knowledge_base 資料夾為空，嘗試載入 products.csv 和 faq.txt")
            products_content = self._load_file_content(self.products_file)
            faq_content = self._load_file_content(self.faq_file)
            knowledge_content = f"### 產品資料\n{products_content}\n\n### 常見問答 (FAQ)\n{faq_content}"

        return self.system_instruction.format(
            knowledge_content=knowledge_content
        )

    def refresh_knowledge_base(self, force_reload: bool = True) -> dict:
        """
        重新載入知識庫並刷新快取

        Args:
            force_reload: 是否強制重新載入

        Returns:
            知識庫載入結果
        """
        # 重新載入知識庫檔案
        result = self.knowledge_loader.load_all(force_reload=force_reload)

        # 刷新 Gemini 快取
        if result.get("success") or result.get("files_count", 0) > 0:
            logger.info("知識庫已更新，正在刷新 Gemini 快取...")
            self.initialize_cache(force_refresh=True)

        return result

    def get_knowledge_status(self) -> dict:
        """取得知識庫狀態"""
        return self.knowledge_loader.get_status()

    def initialize_cache(self, force_refresh: bool = False) -> bool:
        """
        初始化或刷新 Context Cache

        Args:
            force_refresh: 是否強制刷新快取

        Returns:
            是否成功
        """
        with self.lock:
            try:
                # 檢查是否需要刷新
                if not force_refresh and self.cached_content is not None:
                    # 檢查快取是否過期
                    if self.cache_created_at:
                        elapsed = datetime.now() - self.cache_created_at
                        if elapsed < timedelta(hours=self.cache_ttl_hours - 1):
                            logger.info("快取仍然有效，跳過刷新")
                            return True

                # 建構知識庫內容
                knowledge_content = self._build_knowledge_content()

                logger.info("正在建立 Gemini Context Cache...")

                # 建立快取
                self.cached_content = caching.CachedContent.create(
                    model=self.model_name,
                    display_name="shopee-customer-service-cache",
                    system_instruction="你是蝦皮電商客服助手",
                    contents=[knowledge_content],
                    ttl=timedelta(hours=self.cache_ttl_hours)
                )

                # 從快取建立模型
                self.model = genai.GenerativeModel.from_cached_content(
                    cached_content=self.cached_content
                )

                self.cache_created_at = datetime.now()

                logger.info(f"Context Cache 建立成功！")
                logger.info(f"  - Cache Name: {self.cached_content.name}")
                logger.info(f"  - Token Count: {self.cached_content.usage_metadata.total_token_count}")
                logger.info(f"  - Expires: {self.cached_content.expire_time}")

                return True

            except Exception as e:
                logger.error(f"建立 Context Cache 失敗: {e}")
                # 回退到無快取模式
                self._fallback_to_no_cache()
                return False

    def _fallback_to_no_cache(self):
        """回退到無快取模式"""
        try:
            logger.warning("使用無快取模式...")
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=self._build_knowledge_content()
            )
            self.cached_content = None
        except Exception as e:
            logger.error(f"建立模型失敗: {e}")
            self.model = None

    def refresh_cache_if_needed(self) -> bool:
        """檢查並刷新快取（如果需要）"""
        if self.cache_created_at is None:
            return self.initialize_cache()

        elapsed = datetime.now() - self.cache_created_at
        # 提前 1 小時刷新，避免快取過期
        if elapsed >= timedelta(hours=self.cache_ttl_hours - 1):
            logger.info("快取即將過期，正在刷新...")
            return self.initialize_cache(force_refresh=True)

        return True

    def generate_response(self, user_message: str, conversation_history: list = None) -> str:
        """
        生成回覆

        Args:
            user_message: 用戶訊息
            conversation_history: 對話歷史 [{"role": "user/model", "parts": ["..."]}]

        Returns:
            AI 回覆
        """
        try:
            # 確保快取/模型已初始化
            if self.model is None:
                self.initialize_cache()

            if self.model is None:
                return "抱歉，系統暫時無法服務，請稍後再試或聯繫客服人員。"

            # 刷新快取（如果需要）
            self.refresh_cache_if_needed()

            # 建構對話
            if conversation_history:
                chat = self.model.start_chat(history=conversation_history)
                response = chat.send_message(user_message)
            else:
                response = self.model.generate_content(user_message)

            reply = response.text.strip()
            logger.info(f"Gemini 回覆: {reply[:50]}...")

            return reply

        except Exception as e:
            logger.error(f"Gemini 生成回覆失敗: {e}")
            return "感謝您的詢問！這個問題需要專人為您服務，請稍候，我將為您轉接客服人員。"

    def get_cache_status(self) -> dict:
        """取得快取狀態"""
        if self.cached_content is None:
            return {
                "status": "no_cache",
                "message": "快取未建立或使用無快取模式"
            }

        try:
            return {
                "status": "active",
                "cache_name": self.cached_content.name,
                "token_count": self.cached_content.usage_metadata.total_token_count,
                "created_at": self.cache_created_at.isoformat() if self.cache_created_at else None,
                "expires_at": str(self.cached_content.expire_time),
                "model": self.model_name
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }

    def delete_cache(self) -> bool:
        """刪除快取"""
        try:
            if self.cached_content:
                self.cached_content.delete()
                logger.info("快取已刪除")
            self.cached_content = None
            self.model = None
            self.cache_created_at = None
            return True
        except Exception as e:
            logger.error(f"刪除快取失敗: {e}")
            return False


# 全域實例
_gemini_service = None
_service_lock = Lock()


def get_gemini_service() -> GeminiService:
    """取得 Gemini 服務單例"""
    global _gemini_service

    with _service_lock:
        if _gemini_service is None:
            api_key = os.getenv('GEMINI_API_KEY', '')
            knowledge_folder = os.getenv('KNOWLEDGE_FOLDER', 'knowledge_base')
            products_file = os.getenv('PRODUCTS_FILE', 'products.csv')
            faq_file = os.getenv('FAQ_FILE', 'faq.txt')
            model_name = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
            cache_ttl = int(os.getenv('CACHE_TTL_HOURS', '24'))

            _gemini_service = GeminiService(
                api_key=api_key,
                knowledge_folder=knowledge_folder,
                products_file=products_file,
                faq_file=faq_file,
                model_name=model_name,
                cache_ttl_hours=cache_ttl
            )

        return _gemini_service


def initialize_gemini():
    """初始化 Gemini 服務（應用程式啟動時呼叫）"""
    service = get_gemini_service()
    return service.initialize_cache()


def generate_reply(user_message: str, conversation_history: list = None) -> str:
    """生成回覆的快捷函式"""
    service = get_gemini_service()
    return service.generate_response(user_message, conversation_history)


def refresh_knowledge_base() -> dict:
    """重新載入知識庫的快捷函式"""
    service = get_gemini_service()
    return service.refresh_knowledge_base()


def get_knowledge_status() -> dict:
    """取得知識庫狀態的快捷函式"""
    service = get_gemini_service()
    return service.get_knowledge_status()
