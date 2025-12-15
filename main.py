#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮/墨筆客 AI 自動客服系統 - Playwright 公用版 (完整功能版)
Shopee AI Auto Customer Service - Playwright Browser Automation Version

功能：
- 使用 Playwright 瀏覽器自動化 (asyncio 非同步模式)
- 保留登入狀態 (user_data_dir)
- 每隔 30~60 秒隨機刷新檢查未讀訊息
- 模擬真人打字 (每字 0.1~0.3 秒間隔)
- 防封號機制：禁止 Copy-Paste，模擬真人行為
- OpenAI GPT-4o-mini 生成回覆 (含對話上下文)
- 知識庫檔案支援
- 已回覆訊息追蹤 (避免重複回覆)
- 對話歷史記錄
- 統計儀表板

作者：AI Customer Service Bot
版本：2.0.0
"""

import asyncio
import random
import os
import json
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Set

from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeout
from fake_useragent import UserAgent
from openai import OpenAI
from dotenv import load_dotenv

# ============================================
# 載入環境變數與設定日誌
# ============================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('shopee_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# 設定區域
# ============================================

# OpenAI API 設定
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

# 蝦皮聊天頁面網址
SHOPEE_CHAT_URL = os.getenv('SHOPEE_CHAT_URL', 'https://seller.shopee.tw/portal/chatroom')

# 瀏覽器資料目錄
USER_DATA_DIR = os.getenv('USER_DATA_DIR', './browser_data')

# 知識庫檔案路徑
KNOWLEDGE_BASE_FILE = os.getenv('KNOWLEDGE_BASE_FILE', 'knowledge_base.txt')

# 刷新間隔設定 (秒)
REFRESH_MIN_SECONDS = int(os.getenv('REFRESH_MIN_SECONDS', 30))
REFRESH_MAX_SECONDS = int(os.getenv('REFRESH_MAX_SECONDS', 60))

# 打字速度設定 (秒)
TYPING_MIN_DELAY = float(os.getenv('TYPING_MIN_DELAY', 0.1))
TYPING_MAX_DELAY = float(os.getenv('TYPING_MAX_DELAY', 0.3))

# 發送前等待時間 (秒)
SEND_WAIT_MIN = float(os.getenv('SEND_WAIT_MIN', 1.0))
SEND_WAIT_MAX = float(os.getenv('SEND_WAIT_MAX', 3.0))

# 對話歷史保留數量
MAX_CONVERSATION_HISTORY = int(os.getenv('MAX_CONVERSATION_HISTORY', 10))

# AI 系統提示詞
SYSTEM_PROMPT = os.getenv('SYSTEM_PROMPT', '''你是一位親切專業的電商客服人員。請用繁體中文回覆客戶問題。

回覆原則：
1. 回答要簡潔有禮貌，不超過 100 字
2. 使用友善的語氣，可適當使用表情符號
3. 如果不確定答案，請客戶稍等或聯繫人工客服
4. 不要透露你是 AI 或機器人
5. 對於訂單、物流問題，請客戶提供訂單編號以便查詢
''')

# 資料檔案
REPLIED_MESSAGES_FILE = 'replied_messages.json'
CONVERSATION_HISTORY_FILE = 'conversation_history.json'
STATS_FILE = 'bot_stats.json'

# ============================================
# OpenAI 客戶端
# ============================================

openai_client = None

def get_openai_client() -> OpenAI:
    """取得 OpenAI 客戶端"""
    global openai_client
    if openai_client is None:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return openai_client


# ============================================
# 知識庫讀取
# ============================================

def load_knowledge_base() -> str:
    """讀取知識庫檔案"""
    try:
        knowledge_file = Path(KNOWLEDGE_BASE_FILE)
        if knowledge_file.exists():
            content = knowledge_file.read_text(encoding='utf-8')
            logger.info(f"已載入知識庫: {len(content)} 字")
            return content
        else:
            logger.warning(f"知識庫檔案不存在: {KNOWLEDGE_BASE_FILE}")
            return ""
    except Exception as e:
        logger.error(f"讀取知識庫失敗: {e}")
        return ""


# ============================================
# 已回覆訊息追蹤 (避免重複回覆)
# ============================================

class RepliedMessagesTracker:
    """追蹤已回覆的訊息，避免重複回覆"""

    def __init__(self, file_path: str = REPLIED_MESSAGES_FILE):
        self.file_path = file_path
        self.replied_hashes: Set[str] = set()
        self._load()

    def _load(self):
        """從檔案載入已回覆訊息"""
        try:
            if Path(self.file_path).exists():
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.replied_hashes = set(data.get('hashes', []))
                logger.info(f"載入 {len(self.replied_hashes)} 個已回覆訊息記錄")
        except Exception as e:
            logger.error(f"載入已回覆訊息失敗: {e}")
            self.replied_hashes = set()

    def _save(self):
        """儲存已回覆訊息到檔案"""
        try:
            # 只保留最近 1000 個
            hashes_list = list(self.replied_hashes)[-1000:]
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump({'hashes': hashes_list, 'updated_at': datetime.now().isoformat()}, f)
        except Exception as e:
            logger.error(f"儲存已回覆訊息失敗: {e}")

    def _generate_hash(self, conversation_id: str, message: str) -> str:
        """產生訊息的唯一 hash"""
        content = f"{conversation_id}:{message}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def is_replied(self, conversation_id: str, message: str) -> bool:
        """檢查訊息是否已回覆"""
        msg_hash = self._generate_hash(conversation_id, message)
        return msg_hash in self.replied_hashes

    def mark_replied(self, conversation_id: str, message: str):
        """標記訊息為已回覆"""
        msg_hash = self._generate_hash(conversation_id, message)
        self.replied_hashes.add(msg_hash)
        self._save()


# ============================================
# 對話歷史管理 (含上下文)
# ============================================

class ConversationHistoryManager:
    """管理對話歷史，讓 AI 能記住上下文"""

    def __init__(self, file_path: str = CONVERSATION_HISTORY_FILE):
        self.file_path = file_path
        self.conversations: Dict[str, List[dict]] = {}
        self._load()

    def _load(self):
        """載入對話歷史"""
        try:
            if Path(self.file_path).exists():
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self.conversations = json.load(f)
                logger.info(f"載入 {len(self.conversations)} 個對話歷史")
        except Exception as e:
            logger.error(f"載入對話歷史失敗: {e}")
            self.conversations = {}

    def _save(self):
        """儲存對話歷史"""
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.conversations, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"儲存對話歷史失敗: {e}")

    def add_message(self, conversation_id: str, role: str, content: str):
        """新增訊息到對話歷史"""
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = []

        self.conversations[conversation_id].append({
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        })

        # 只保留最近 N 則
        if len(self.conversations[conversation_id]) > MAX_CONVERSATION_HISTORY * 2:
            self.conversations[conversation_id] = self.conversations[conversation_id][-MAX_CONVERSATION_HISTORY * 2:]

        self._save()

    def get_history(self, conversation_id: str) -> List[dict]:
        """取得對話歷史 (OpenAI 格式)"""
        if conversation_id not in self.conversations:
            return []

        return [
            {'role': msg['role'], 'content': msg['content']}
            for msg in self.conversations[conversation_id][-MAX_CONVERSATION_HISTORY * 2:]
        ]


# ============================================
# 統計追蹤
# ============================================

class StatsTracker:
    """統計追蹤"""

    def __init__(self, file_path: str = STATS_FILE):
        self.file_path = file_path
        self.stats = {
            'total_messages': 0,
            'total_replies': 0,
            'start_time': None,
            'last_reply_time': None,
            'errors': 0
        }
        self._load()

    def _load(self):
        """載入統計"""
        try:
            if Path(self.file_path).exists():
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self.stats = json.load(f)
        except Exception as e:
            logger.error(f"載入統計失敗: {e}")

    def _save(self):
        """儲存統計"""
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"儲存統計失敗: {e}")

    def record_start(self):
        """記錄啟動時間"""
        self.stats['start_time'] = datetime.now().isoformat()
        self._save()

    def record_reply(self):
        """記錄回覆"""
        self.stats['total_replies'] += 1
        self.stats['last_reply_time'] = datetime.now().isoformat()
        self._save()

    def record_error(self):
        """記錄錯誤"""
        self.stats['errors'] += 1
        self._save()

    def get_summary(self) -> str:
        """取得統計摘要"""
        return f"""
📊 機器人統計
─────────────────
總回覆數: {self.stats['total_replies']}
錯誤次數: {self.stats['errors']}
啟動時間: {self.stats.get('start_time', 'N/A')}
最後回覆: {self.stats.get('last_reply_time', 'N/A')}
"""


# ============================================
# AI 回覆生成 (含對話上下文)
# ============================================

def generate_ai_response(
    customer_message: str,
    knowledge_base: str = "",
    conversation_history: List[dict] = None
) -> str:
    """使用 OpenAI GPT-4o-mini 生成回覆"""
    try:
        client = get_openai_client()

        # 組合系統提示詞
        system_content = SYSTEM_PROMPT
        if knowledge_base:
            system_content += f"\n\n【參考知識庫】\n{knowledge_base}"

        # 組合訊息 (含歷史上下文)
        messages = [{"role": "system", "content": system_content}]

        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": customer_message})

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=200,
            temperature=0.7
        )

        reply = response.choices[0].message.content.strip()
        logger.info(f"AI 回覆: {reply[:50]}...")
        return reply

    except Exception as e:
        logger.error(f"OpenAI API 呼叫失敗: {e}")
        return "您好，感謝您的訊息！客服人員稍後會為您處理，請稍等～"


# ============================================
# 模擬真人打字 (防封號核心)
# ============================================

async def simulate_human_typing(page: Page, element, text: str):
    """
    模擬真人打字行為

    【防封號關鍵】
    - 禁止使用 paste/fill 直接貼上
    - 每字間隔 0.1~0.3 秒
    - 偶爾加入思考停頓
    - 偶爾打錯字再刪除 (更像真人)
    """
    logger.info(f"開始打字: {text[:30]}...")

    # 點擊元素確保焦點
    await element.click()
    await asyncio.sleep(random.uniform(0.3, 0.6))

    for i, char in enumerate(text):
        # 輸入字元
        await page.keyboard.type(char, delay=0)

        # 基本延遲
        delay = random.uniform(TYPING_MIN_DELAY, TYPING_MAX_DELAY)
        await asyncio.sleep(delay)

        # 5% 機率加入思考停頓
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.4, 0.8))

        # 2% 機率打錯字再刪除 (更像真人)
        if random.random() < 0.02 and i < len(text) - 1:
            wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
            await page.keyboard.type(wrong_char, delay=0)
            await asyncio.sleep(random.uniform(0.2, 0.4))
            await page.keyboard.press('Backspace')
            await asyncio.sleep(random.uniform(0.1, 0.2))

    logger.info("打字完成")


async def human_like_send(page: Page, send_button):
    """模擬真人發送訊息"""
    # 發送前隨機等待 (模擬檢查訊息)
    wait_time = random.uniform(SEND_WAIT_MIN, SEND_WAIT_MAX)
    logger.info(f"等待 {wait_time:.1f} 秒後發送...")
    await asyncio.sleep(wait_time)

    # 移動滑鼠到按鈕 (更像真人)
    await send_button.hover()
    await asyncio.sleep(random.uniform(0.1, 0.3))

    # 點擊發送
    await send_button.click()
    logger.info("已發送訊息")


# ============================================
# 蝦皮頁面選擇器
# ============================================

class ShopeeSelectors:
    """
    蝦皮賣家中心聊天頁面 CSS 選擇器

    ⚠️ 這些選擇器基於蝦皮賣家中心的常見結構
    如果不起作用，請用 F12 開發者工具檢查實際結構
    """

    # ===== 聊天列表區域 =====
    # 聊天列表容器
    CHAT_LIST_CONTAINER = '.chat-list, [class*="conversation-list"], [class*="chatList"]'

    # 單一對話項目
    CHAT_ITEM = '.chat-item, [class*="conversation-item"], [class*="chatItem"], [class*="chat_item"]'

    # 未讀對話 (通常有紅點或特殊樣式)
    UNREAD_CHAT = '[class*="unread"], [class*="has-new"], [class*="new-message"], [class*="hasUnread"]'

    # 未讀數量徽章
    UNREAD_BADGE = '.unread-badge, [class*="badge"], [class*="unread-count"], [class*="msg-count"]'

    # ===== 訊息區域 =====
    # 訊息容器
    MESSAGE_CONTAINER = '.message-list, [class*="message-container"], [class*="chatContent"]'

    # 所有訊息
    ALL_MESSAGES = '.message-item, [class*="message-bubble"], [class*="chat-message"]'

    # 買家發送的訊息 (對方)
    BUYER_MESSAGE = '[class*="buyer"], [class*="received"], [class*="left"], [class*="other"], [class*="customer"]'

    # 訊息文字內容
    MESSAGE_TEXT = '.message-text, [class*="message-content"], [class*="text-content"], [class*="msg-text"]'

    # ===== 輸入區域 =====
    # 輸入框 (可能是 textarea, input, 或 contenteditable div)
    INPUT_BOX = 'textarea[class*="input"], input[class*="message"], [contenteditable="true"], .chat-input, [class*="editor"], [class*="textArea"]'

    # 發送按鈕
    SEND_BUTTON = 'button[class*="send"], [class*="send-btn"], [class*="submit"], button[type="submit"], [class*="sendBtn"]'

    # ===== 備用選擇器 (更寬鬆) =====
    FALLBACK_INPUT = 'textarea, [contenteditable="true"], input[type="text"]'
    FALLBACK_SEND = 'button:has-text("發送"), button:has-text("Send"), button:has-text("傳送")'


# ============================================
# 蝦皮聊天機器人主類
# ============================================

class ShopeeChatBot:
    """蝦皮聊天機器人 - 完整功能版"""

    def __init__(self):
        self.context: BrowserContext = None
        self.page: Page = None
        self.playwright = None
        self.knowledge_base: str = ""
        self.is_running: bool = False

        # 追蹤器
        self.replied_tracker = RepliedMessagesTracker()
        self.history_manager = ConversationHistoryManager()
        self.stats = StatsTracker()

        # 當前對話 ID
        self.current_conversation_id: str = ""

    async def initialize(self):
        """初始化瀏覽器"""
        logger.info("=" * 60)
        logger.info("🚀 蝦皮 AI 自動客服系統啟動中...")
        logger.info("=" * 60)

        # 載入知識庫
        self.knowledge_base = load_knowledge_base()

        # 產生隨機 User-Agent
        ua = UserAgent()
        user_agent = ua.random
        logger.info(f"User-Agent: {user_agent[:60]}...")

        # 確保目錄存在
        Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)

        # 啟動 Playwright
        self.playwright = await async_playwright().start()

        # 啟動瀏覽器 (持久化 context)
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            slow_mo=50,  # 稍微放慢操作速度
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-infobars',
                '--disable-extensions',
            ],
            user_agent=user_agent,
            viewport={'width': 1366, 'height': 768},
            locale='zh-TW',
            timezone_id='Asia/Taipei',
            ignore_https_errors=True,
        )

        # 移除 webdriver 標記
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        # 取得或建立頁面
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        # 記錄啟動
        self.stats.record_start()
        logger.info("✅ 瀏覽器初始化完成")

    async def navigate_to_chat(self):
        """導航到聊天頁面"""
        logger.info(f"正在導航至: {SHOPEE_CHAT_URL}")

        try:
            await self.page.goto(SHOPEE_CHAT_URL, wait_until='networkidle', timeout=60000)
        except PlaywrightTimeout:
            logger.warning("頁面載入超時，繼續執行...")

        await asyncio.sleep(3)

        # 檢查是否需要登入
        current_url = self.page.url
        if 'login' in current_url.lower() or 'signin' in current_url.lower() or 'account' in current_url.lower():
            logger.warning("=" * 60)
            logger.warning("⚠️  需要登入！請在瀏覽器中手動登入")
            logger.warning("    登入完成後，按 Enter 繼續...")
            logger.warning("=" * 60)
            input("\n👉 按 Enter 繼續...")
            await asyncio.sleep(2)

            # 重新導航
            try:
                await self.page.goto(SHOPEE_CHAT_URL, wait_until='networkidle', timeout=60000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(3)

        logger.info("✅ 已進入聊天頁面")

    async def find_element_with_fallback(self, selectors: list, timeout: int = 5000):
        """嘗試多個選擇器找元素"""
        for selector in selectors:
            try:
                element = await self.page.wait_for_selector(selector, timeout=timeout, state='visible')
                if element:
                    return element
            except:
                continue
        return None

    async def find_unread_conversation(self):
        """找到未讀對話"""
        try:
            # 嘗試各種未讀選擇器
            unread_selectors = [
                ShopeeSelectors.UNREAD_CHAT,
                '[class*="unread"]',
                '[class*="new"]',
                '.has-unread',
            ]

            for selector in unread_selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    if elements and len(elements) > 0:
                        logger.info(f"找到 {len(elements)} 個未讀對話")
                        return elements[0]
                except:
                    continue

            # 檢查是否有未讀徽章
            badge = await self.page.query_selector(ShopeeSelectors.UNREAD_BADGE)
            if badge:
                badge_text = await badge.inner_text()
                if badge_text and badge_text.strip() and badge_text.strip() != '0':
                    logger.info(f"發現未讀徽章: {badge_text}")
                    # 找到對應的對話項目
                    parent = await badge.evaluate_handle('el => el.closest("[class*=\\"chat\\"], [class*=\\"conversation\\"]")')
                    if parent:
                        return parent

            return None

        except Exception as e:
            logger.error(f"查找未讀對話錯誤: {e}")
            return None

    async def get_conversation_id(self) -> str:
        """取得當前對話 ID (用於追蹤)"""
        try:
            # 嘗試從 URL 取得
            url = self.page.url
            if 'conversation' in url or 'chat' in url:
                # 提取數字 ID
                import re
                match = re.search(r'[/=](\d{10,})', url)
                if match:
                    return match.group(1)

            # 嘗試從頁面元素取得
            active_chat = await self.page.query_selector('[class*="active"], [class*="selected"]')
            if active_chat:
                chat_id = await active_chat.get_attribute('data-id') or await active_chat.get_attribute('id')
                if chat_id:
                    return chat_id

            # 使用時間戳作為備用
            return f"conv_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        except Exception as e:
            logger.error(f"取得對話 ID 失敗: {e}")
            return f"conv_{datetime.now().timestamp()}"

    async def get_latest_buyer_message(self) -> Optional[str]:
        """取得最新的買家訊息"""
        try:
            # 等待訊息載入
            await asyncio.sleep(1)

            # 嘗試找買家訊息
            buyer_selectors = [
                ShopeeSelectors.BUYER_MESSAGE,
                '[class*="received"]',
                '[class*="left"]',
                '[class*="other"]',
                '[class*="buyer"]',
            ]

            for selector in buyer_selectors:
                try:
                    messages = await self.page.query_selector_all(selector)
                    if messages and len(messages) > 0:
                        last_msg = messages[-1]
                        # 找文字內容
                        text_el = await last_msg.query_selector(ShopeeSelectors.MESSAGE_TEXT)
                        if text_el:
                            text = await text_el.inner_text()
                        else:
                            text = await last_msg.inner_text()

                        if text and text.strip():
                            return text.strip()
                except:
                    continue

            # 備用：取得所有訊息中最後一個
            all_messages = await self.page.query_selector_all(ShopeeSelectors.ALL_MESSAGES)
            if all_messages and len(all_messages) > 0:
                last = all_messages[-1]
                text = await last.inner_text()
                if text:
                    return text.strip()

            return None

        except Exception as e:
            logger.error(f"取得買家訊息錯誤: {e}")
            return None

    async def find_input_and_send(self):
        """找到輸入框和發送按鈕"""
        input_box = None
        send_button = None

        # 找輸入框
        input_selectors = [
            ShopeeSelectors.INPUT_BOX,
            'textarea',
            '[contenteditable="true"]',
            'input[type="text"]',
            '[class*="editor"]',
            '[class*="input"]',
        ]

        for selector in input_selectors:
            try:
                el = await self.page.query_selector(selector)
                if el:
                    is_visible = await el.is_visible()
                    if is_visible:
                        input_box = el
                        logger.info(f"找到輸入框: {selector}")
                        break
            except:
                continue

        # 找發送按鈕
        send_selectors = [
            ShopeeSelectors.SEND_BUTTON,
            'button:has-text("發送")',
            'button:has-text("Send")',
            'button:has-text("傳送")',
            '[class*="send"]',
            'button[type="submit"]',
        ]

        for selector in send_selectors:
            try:
                el = await self.page.query_selector(selector)
                if el:
                    is_visible = await el.is_visible()
                    if is_visible:
                        send_button = el
                        logger.info(f"找到發送按鈕: {selector}")
                        break
            except:
                continue

        return input_box, send_button

    async def send_message(self, message: str) -> bool:
        """發送訊息"""
        try:
            input_box, send_button = await self.find_input_and_send()

            if not input_box:
                logger.error("❌ 找不到輸入框")
                return False

            if not send_button:
                logger.error("❌ 找不到發送按鈕")
                return False

            # 模擬真人打字
            await simulate_human_typing(self.page, input_box, message)

            # 模擬真人發送
            await human_like_send(self.page, send_button)

            # 記錄統計
            self.stats.record_reply()

            return True

        except Exception as e:
            logger.error(f"發送訊息錯誤: {e}")
            self.stats.record_error()
            return False

    async def process_conversation(self):
        """處理一個對話"""
        try:
            # 取得對話 ID
            self.current_conversation_id = await self.get_conversation_id()
            logger.info(f"處理對話: {self.current_conversation_id}")

            # 取得最新買家訊息
            customer_message = await self.get_latest_buyer_message()

            if not customer_message:
                logger.warning("無法取得客戶訊息")
                return

            logger.info(f"客戶訊息: {customer_message[:50]}...")

            # 檢查是否已回覆
            if self.replied_tracker.is_replied(self.current_conversation_id, customer_message):
                logger.info("此訊息已回覆過，跳過")
                return

            # 取得對話歷史
            history = self.history_manager.get_history(self.current_conversation_id)

            # 生成 AI 回覆
            ai_reply = generate_ai_response(customer_message, self.knowledge_base, history)

            # 發送回覆
            success = await self.send_message(ai_reply)

            if success:
                # 記錄為已回覆
                self.replied_tracker.mark_replied(self.current_conversation_id, customer_message)

                # 更新對話歷史
                self.history_manager.add_message(self.current_conversation_id, 'user', customer_message)
                self.history_manager.add_message(self.current_conversation_id, 'assistant', ai_reply)

                # 記錄對話日誌
                self._log_conversation(customer_message, ai_reply)

                logger.info(f"✅ 已回覆: {ai_reply[:40]}...")

        except Exception as e:
            logger.error(f"處理對話錯誤: {e}")
            self.stats.record_error()

    def _log_conversation(self, customer_msg: str, bot_reply: str):
        """記錄對話到日誌檔案"""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_entry = f"""
{'='*60}
[{timestamp}] 對話 ID: {self.current_conversation_id}
─────────────────────────────────────────────────────────────
👤 客戶: {customer_msg}
🤖 AI: {bot_reply}
{'='*60}
"""
            with open('conversation_log.txt', 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            logger.error(f"記錄對話失敗: {e}")

    async def check_and_process_unread(self) -> bool:
        """檢查並處理未讀訊息"""
        try:
            # 找未讀對話
            unread = await self.find_unread_conversation()

            if unread:
                logger.info("📨 發現未讀對話，點擊進入...")
                await unread.click()
                await asyncio.sleep(2)

                # 處理對話
                await self.process_conversation()
                return True

            return False

        except Exception as e:
            logger.error(f"檢查未讀錯誤: {e}")
            return False

    async def main_loop(self):
        """主監控循環"""
        logger.info("=" * 60)
        logger.info("🔄 開始監控未讀訊息...")
        logger.info(f"   刷新間隔: {REFRESH_MIN_SECONDS}~{REFRESH_MAX_SECONDS} 秒")
        logger.info("   按 Ctrl+C 停止")
        logger.info("=" * 60)

        self.is_running = True
        check_count = 0

        while self.is_running:
            try:
                check_count += 1
                logger.info(f"\n[第 {check_count} 次檢查]")

                # 檢查並處理未讀
                has_unread = await self.check_and_process_unread()

                if not has_unread:
                    logger.info("📭 沒有新訊息")

                # 顯示統計 (每 10 次)
                if check_count % 10 == 0:
                    print(self.stats.get_summary())

                # 隨機等待
                wait_time = random.randint(REFRESH_MIN_SECONDS, REFRESH_MAX_SECONDS)
                logger.info(f"⏳ 等待 {wait_time} 秒...")
                await asyncio.sleep(wait_time)

                # 刷新頁面
                try:
                    await self.page.reload(wait_until='networkidle', timeout=30000)
                except PlaywrightTimeout:
                    logger.warning("頁面刷新超時，繼續...")

                await asyncio.sleep(2)

            except KeyboardInterrupt:
                logger.info("\n⏹️ 收到停止訊號...")
                break
            except Exception as e:
                logger.error(f"監控循環錯誤: {e}")
                self.stats.record_error()
                await asyncio.sleep(10)

        logger.info("監控已停止")
        print(self.stats.get_summary())

    async def run(self):
        """執行機器人"""
        try:
            await self.initialize()
            await self.navigate_to_chat()
            await self.main_loop()
        except KeyboardInterrupt:
            logger.info("程式被中斷")
        except Exception as e:
            logger.error(f"程式錯誤: {e}")
        finally:
            if self.context:
                await self.context.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("程式結束")


# ============================================
# 程式入口
# ============================================

async def main():
    """主程式入口"""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║     🛒 蝦皮/墨筆客 AI 自動客服系統 v2.0                          ║
║        Playwright 公用版 - 完整功能                              ║
║                                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  ⚠️  使用前注意事項：                                            ║
║                                                                  ║
║  1. 請先用測試帳號運行                                           ║
║  2. 前 100 則訊息請務必監看                                      ║
║  3. 發現異常立即按 Ctrl+C 停止                                   ║
║  4. 第一次執行前請先手動登入蝦皮                                 ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # 驗證 API Key
    if not OPENAI_API_KEY or OPENAI_API_KEY == 'your-openai-api-key-here':
        print("\n❌ 錯誤：請先設定 OPENAI_API_KEY")
        print("   1. 複製 .env.example 為 .env")
        print("   2. 在 .env 中填入你的 OpenAI API Key")
        print("\n   取得 API Key: https://platform.openai.com/api-keys")
        return

    print("\n✅ API Key 已設定")
    print(f"✅ 聊天頁面: {SHOPEE_CHAT_URL}")
    print(f"✅ AI 模型: {OPENAI_MODEL}")
    print(f"✅ 刷新間隔: {REFRESH_MIN_SECONDS}-{REFRESH_MAX_SECONDS} 秒")
    print("\n" + "="*60)
    input("\n👉 按 Enter 開始執行...\n")

    # 執行機器人
    bot = ShopeeChatBot()
    await bot.run()


if __name__ == '__main__':
    asyncio.run(main())
