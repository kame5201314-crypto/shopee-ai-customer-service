#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服機器人 - 核心模組
使用 Playwright 自動化瀏覽器操作
整合 Gemini AI 生成回覆
"""

import os
import re
import json
import random
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from threading import Event

from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from fake_useragent import UserAgent

# Gemini 服務
from gemini_service import get_gemini_service, generate_reply

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================
# 常數設定
# ============================================

# 蝦皮賣家聊天頁面
DEFAULT_CHAT_URL = "https://seller.shopee.tw/portal/chatroom"

# 選擇器 (需根據蝦皮實際頁面調整)
SELECTORS = {
    # 聊天列表
    "chat_list": ".chat-list-item, [data-testid='chat-list-item']",
    "unread_badge": ".unread-badge, .badge, [class*='unread']",

    # 聊天視窗
    "chat_window": ".chat-window, .conversation-panel",
    "message_input": "textarea[placeholder*='輸入'], .chat-input textarea, [data-testid='message-input']",
    "send_button": "button[type='submit'], .send-btn, [data-testid='send-button']",

    # 訊息
    "messages": ".message-item, .chat-message, [data-testid='message']",
    "customer_message": ".message-item.received, .incoming-message",
    "last_message": ".message-item:last-child, .chat-message:last-child",
}

# 時間設定 (秒)
TIMING = {
    "page_load_wait": 5,
    "check_interval_min": 30,
    "check_interval_max": 60,
    "typing_delay_min": 0.05,
    "typing_delay_max": 0.15,
    "send_wait_min": 1.0,
    "send_wait_max": 3.0,
    "between_messages_min": 2.0,
    "between_messages_max": 5.0,
}


# ============================================
# 輔助函式
# ============================================

def load_timing_config():
    """從環境變數載入時間設定"""
    global TIMING
    TIMING.update({
        "check_interval_min": int(os.getenv("REFRESH_MIN_SECONDS", 30)),
        "check_interval_max": int(os.getenv("REFRESH_MAX_SECONDS", 60)),
        "typing_delay_min": float(os.getenv("TYPING_MIN_DELAY", 0.05)),
        "typing_delay_max": float(os.getenv("TYPING_MAX_DELAY", 0.15)),
        "send_wait_min": float(os.getenv("SEND_WAIT_MIN", 1.0)),
        "send_wait_max": float(os.getenv("SEND_WAIT_MAX", 3.0)),
    })


def random_delay(min_sec: float, max_sec: float) -> float:
    """產生隨機延遲時間"""
    return random.uniform(min_sec, max_sec)


# ============================================
# 蝦皮機器人類別
# ============================================

class ShopeeBot:
    """蝦皮 AI 客服機器人"""

    def __init__(self):
        """初始化機器人"""
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        self.chat_url = os.getenv("SHOPEE_CHAT_URL", DEFAULT_CHAT_URL)
        self.headless = os.getenv("HEADLESS", "false").lower() == "true"
        self.user_data_dir = Path("browser_data")

        # 統計
        self.messages_processed = 0
        self.replies_sent = 0
        self.errors_count = 0

        # 已處理的訊息 ID (避免重複回覆)
        self.processed_messages: set = set()

        # 載入時間設定
        load_timing_config()

        logger.info("ShopeeBot 初始化完成")

    async def setup_browser(self):
        """設定瀏覽器"""
        logger.info("正在啟動瀏覽器...")

        playwright = await async_playwright().start()

        # 取得隨機 User-Agent
        try:
            ua = UserAgent()
            user_agent = ua.chrome
        except:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        # 確保資料目錄存在
        self.user_data_dir.mkdir(exist_ok=True)

        # 啟動瀏覽器 (使用持久化 context 保留登入狀態)
        self.context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            viewport={"width": 1366, "height": 768},
            user_agent=user_agent,
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
        )

        # 取得頁面
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        # 隱藏自動化特徵
        await self._hide_automation()

        logger.info("瀏覽器啟動成功")

    async def _hide_automation(self):
        """隱藏自動化特徵"""
        await self.page.add_init_script("""
            // 移除 webdriver 標記
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // 模擬真實的 plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // 模擬真實的語言
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-TW', 'zh', 'en-US', 'en']
            });
        """)

    async def navigate_to_chat(self):
        """導航到聊天頁面"""
        logger.info(f"正在前往: {self.chat_url}")

        await self.page.goto(self.chat_url, wait_until="networkidle")
        await asyncio.sleep(TIMING["page_load_wait"])

        # 檢查是否需要登入
        current_url = self.page.url

        if "login" in current_url.lower() or "signin" in current_url.lower():
            logger.warning("偵測到登入頁面，請在瀏覽器中手動登入...")
            logger.info("登入完成後，機器人將自動繼續運作")

            # 等待用戶登入 (最多 5 分鐘)
            for _ in range(60):
                await asyncio.sleep(5)
                current_url = self.page.url
                if "chatroom" in current_url.lower() or "chat" in current_url.lower():
                    logger.info("登入成功！")
                    break
            else:
                raise Exception("登入超時，請重新啟動機器人")

        logger.info("已進入聊天頁面")

    async def get_unread_chats(self) -> List[Dict]:
        """取得未讀訊息的聊天"""
        unread_chats = []

        try:
            # 等待聊天列表載入
            await self.page.wait_for_selector(SELECTORS["chat_list"], timeout=10000)

            # 取得所有聊天項目
            chat_items = await self.page.query_selector_all(SELECTORS["chat_list"])

            for item in chat_items:
                # 檢查是否有未讀標記
                unread_badge = await item.query_selector(SELECTORS["unread_badge"])

                if unread_badge:
                    # 取得聊天資訊
                    chat_id = await item.get_attribute("data-id") or str(id(item))

                    # 避免處理已處理過的
                    if chat_id not in self.processed_messages:
                        unread_chats.append({
                            "id": chat_id,
                            "element": item
                        })

        except Exception as e:
            logger.debug(f"取得未讀訊息時發生錯誤: {e}")

        return unread_chats

    async def get_last_customer_message(self) -> Optional[str]:
        """取得客戶最後一則訊息"""
        try:
            # 等待訊息載入
            await asyncio.sleep(1)

            # 取得所有客戶訊息
            messages = await self.page.query_selector_all(SELECTORS["customer_message"])

            if messages:
                last_message = messages[-1]
                text = await last_message.inner_text()
                return text.strip()

        except Exception as e:
            logger.debug(f"取得客戶訊息時發生錯誤: {e}")

        return None

    async def type_message(self, message: str):
        """模擬真人打字"""
        input_selector = SELECTORS["message_input"]

        try:
            # 等待輸入框
            await self.page.wait_for_selector(input_selector, timeout=5000)
            input_box = await self.page.query_selector(input_selector)

            if not input_box:
                raise Exception("找不到訊息輸入框")

            # 點擊輸入框
            await input_box.click()
            await asyncio.sleep(0.3)

            # 逐字輸入 (模擬真人打字)
            for char in message:
                await input_box.type(char, delay=random_delay(
                    TIMING["typing_delay_min"] * 1000,
                    TIMING["typing_delay_max"] * 1000
                ))

                # 偶爾模擬打錯字再刪除
                if os.getenv("TYPO_SIMULATION", "true").lower() == "true":
                    if random.random() < 0.02:  # 2% 機率打錯
                        wrong_char = random.choice("abcdefghijklmnopqrstuvwxyz")
                        await input_box.type(wrong_char)
                        await asyncio.sleep(random_delay(0.2, 0.5))
                        await self.page.keyboard.press("Backspace")

            logger.info(f"已輸入訊息: {message[:30]}...")

        except Exception as e:
            logger.error(f"輸入訊息時發生錯誤: {e}")
            raise

    async def send_message(self):
        """發送訊息"""
        try:
            # 發送前等待
            await asyncio.sleep(random_delay(
                TIMING["send_wait_min"],
                TIMING["send_wait_max"]
            ))

            # 點擊發送按鈕或按 Enter
            send_button = await self.page.query_selector(SELECTORS["send_button"])

            if send_button:
                await send_button.click()
            else:
                await self.page.keyboard.press("Enter")

            logger.info("訊息已發送")
            self.replies_sent += 1

        except Exception as e:
            logger.error(f"發送訊息時發生錯誤: {e}")
            raise

    async def process_chat(self, chat_info: Dict):
        """處理單一聊天"""
        chat_id = chat_info["id"]
        chat_element = chat_info["element"]

        try:
            # 點擊進入聊天
            await chat_element.click()
            await asyncio.sleep(1.5)

            # 取得客戶訊息
            customer_message = await self.get_last_customer_message()

            if not customer_message:
                logger.debug(f"聊天 {chat_id}: 無法取得客戶訊息")
                return

            logger.info(f"客戶訊息: {customer_message[:50]}...")
            self.messages_processed += 1

            # 使用 Gemini 生成回覆
            try:
                reply = generate_reply(customer_message)
                logger.info(f"AI 回覆: {reply[:50]}...")
            except Exception as e:
                logger.error(f"Gemini 生成回覆失敗: {e}")
                reply = "感謝您的詢問！這個問題需要專人為您服務，請稍候，我將為您轉接客服人員。"

            # 輸入並發送回覆
            await self.type_message(reply)
            await self.send_message()

            # 標記為已處理
            self.processed_messages.add(chat_id)

            logger.info(f"聊天 {chat_id} 處理完成")

        except Exception as e:
            logger.error(f"處理聊天 {chat_id} 時發生錯誤: {e}")
            self.errors_count += 1

    async def run(self, stop_event: Event):
        """主循環"""
        try:
            # 設定瀏覽器
            await self.setup_browser()

            # 前往聊天頁面
            await self.navigate_to_chat()

            logger.info("機器人開始運作...")
            logger.info(f"檢查間隔: {TIMING['check_interval_min']}-{TIMING['check_interval_max']} 秒")

            # 主循環
            while not stop_event.is_set():
                try:
                    # 檢查未讀訊息
                    unread_chats = await self.get_unread_chats()

                    if unread_chats:
                        logger.info(f"發現 {len(unread_chats)} 個未讀聊天")

                        for chat in unread_chats:
                            if stop_event.is_set():
                                break

                            await self.process_chat(chat)

                            # 處理間隔
                            await asyncio.sleep(random_delay(
                                TIMING["between_messages_min"],
                                TIMING["between_messages_max"]
                            ))

                    # 等待下一次檢查
                    wait_time = random_delay(
                        TIMING["check_interval_min"],
                        TIMING["check_interval_max"]
                    )
                    logger.debug(f"等待 {wait_time:.0f} 秒後檢查...")

                    # 分段等待，以便能及時回應停止信號
                    for _ in range(int(wait_time)):
                        if stop_event.is_set():
                            break
                        await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"循環中發生錯誤: {e}")
                    self.errors_count += 1
                    await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"機器人執行失敗: {e}")
            raise

        finally:
            await self.cleanup()

    async def cleanup(self):
        """清理資源"""
        logger.info("正在清理資源...")

        try:
            if self.context:
                await self.context.close()
        except:
            pass

        logger.info(f"統計: 處理 {self.messages_processed} 則訊息, 回覆 {self.replies_sent} 則, 錯誤 {self.errors_count} 次")

    def get_stats(self) -> Dict:
        """取得統計資料"""
        return {
            "messages_processed": self.messages_processed,
            "replies_sent": self.replies_sent,
            "errors_count": self.errors_count,
        }


# ============================================
# 主程式 (直接執行時使用)
# ============================================

async def main():
    """主程式"""
    from dotenv import load_dotenv
    load_dotenv()

    bot = ShopeeBot()
    stop_event = Event()

    try:
        await bot.run(stop_event)
    except KeyboardInterrupt:
        logger.info("收到停止信號...")
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
