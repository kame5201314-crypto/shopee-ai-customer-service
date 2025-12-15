#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 自動客服系統 - 完整版
Shopee AI Auto Customer Service System - Full Version

功能：
- OAuth 2.0 授權流程
- Token 自動刷新機制
- Webhook 訊息監聽
- OpenAI GPT-4o 自動回覆（含對話上下文）
- 關鍵字自動回覆
- 訊息記錄與查詢
- 速率限制防護
- 多種訊息類型支援
"""

import os
import json
import time
import hmac
import hashlib
import logging
import re
from threading import Lock
from datetime import datetime
from collections import defaultdict
from functools import wraps

import requests
from flask import Flask, request, redirect, jsonify, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from openai import OpenAI

# ============================================
# 初始化設定
# ============================================

# 載入環境變數
load_dotenv()

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('shopee_ai.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask 應用程式
app = Flask(__name__)

# ============================================
# 環境變數讀取
# ============================================

SHOPEE_PARTNER_ID = int(os.getenv('SHOPEE_PARTNER_ID', 0))
SHOPEE_PARTNER_KEY = os.getenv('SHOPEE_PARTNER_KEY', '')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
APP_PORT = int(os.getenv('APP_PORT', 5000))
SYSTEM_PROMPT = os.getenv('SYSTEM_PROMPT', '你是一位親切專業的電商客服人員，請用繁體中文回覆客戶問題。回答要簡潔有禮貌，不超過100字。')
REDIRECT_URL = os.getenv('REDIRECT_URL', f'http://localhost:{APP_PORT}/auth/callback')

# 進階設定
MAX_CONVERSATION_HISTORY = int(os.getenv('MAX_CONVERSATION_HISTORY', 10))  # 保留對話歷史數量
RATE_LIMIT_PER_MINUTE = int(os.getenv('RATE_LIMIT_PER_MINUTE', 30))  # 每分鐘最大請求數
ENABLE_KEYWORD_REPLY = os.getenv('ENABLE_KEYWORD_REPLY', 'true').lower() == 'true'

# 蝦皮 API 基礎網址
SHOPEE_API_BASE = 'https://partner.shopeemobile.com'

# 檔案路徑
TOKEN_FILE = 'tokens.json'
CONVERSATIONS_FILE = 'conversations.json'
MESSAGES_LOG_FILE = 'messages_log.json'
KEYWORD_RULES_FILE = 'keyword_rules.json'

# 線程安全鎖
token_lock = Lock()
conversation_lock = Lock()
message_lock = Lock()

# 速率限制追蹤
rate_limit_tracker = defaultdict(list)
rate_limit_lock = Lock()

# OpenAI 客戶端（延遲初始化）
openai_client = None

def get_openai_client():
    """延遲初始化 OpenAI 客戶端"""
    global openai_client
    if openai_client is None and OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return openai_client


# ============================================
# 速率限制裝飾器
# ============================================

def rate_limit(func):
    """速率限制裝飾器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        client_ip = request.remote_addr
        current_time = time.time()

        with rate_limit_lock:
            # 清除超過 1 分鐘的記錄
            rate_limit_tracker[client_ip] = [
                t for t in rate_limit_tracker[client_ip]
                if current_time - t < 60
            ]

            # 檢查是否超過限制
            if len(rate_limit_tracker[client_ip]) >= RATE_LIMIT_PER_MINUTE:
                logger.warning(f"速率限制觸發: {client_ip}")
                return jsonify({'error': 'Rate limit exceeded'}), 429

            # 記錄此次請求
            rate_limit_tracker[client_ip].append(current_time)

        return func(*args, **kwargs)
    return wrapper


# ============================================
# Token 檔案操作 (線程安全)
# ============================================

def read_tokens() -> dict:
    """線程安全讀取 Token 檔案"""
    with token_lock:
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"讀取 Token 檔案失敗: {e}")
    return {}


def write_tokens(data: dict) -> bool:
    """線程安全寫入 Token 檔案"""
    with token_lock:
        try:
            with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("Token 已更新並儲存")
            return True
        except Exception as e:
            logger.error(f"寫入 Token 檔案失敗: {e}")
            return False


# ============================================
# 對話歷史管理 (線程安全)
# ============================================

def read_conversations() -> dict:
    """讀取對話歷史"""
    with conversation_lock:
        try:
            if os.path.exists(CONVERSATIONS_FILE):
                with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"讀取對話歷史失敗: {e}")
    return {}


def write_conversations(data: dict) -> bool:
    """寫入對話歷史"""
    with conversation_lock:
        try:
            with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"寫入對話歷史失敗: {e}")
            return False


def add_to_conversation(user_id: str, role: str, content: str):
    """新增訊息到對話歷史"""
    conversations = read_conversations()

    if user_id not in conversations:
        conversations[user_id] = {
            'messages': [],
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }

    conversations[user_id]['messages'].append({
        'role': role,
        'content': content,
        'timestamp': datetime.now().isoformat()
    })

    # 保留最近的對話
    if len(conversations[user_id]['messages']) > MAX_CONVERSATION_HISTORY * 2:
        conversations[user_id]['messages'] = conversations[user_id]['messages'][-MAX_CONVERSATION_HISTORY * 2:]

    conversations[user_id]['updated_at'] = datetime.now().isoformat()
    write_conversations(conversations)


def get_conversation_history(user_id: str) -> list:
    """取得對話歷史（用於 OpenAI）"""
    conversations = read_conversations()

    if user_id not in conversations:
        return []

    messages = conversations[user_id].get('messages', [])
    # 轉換格式給 OpenAI
    return [{'role': msg['role'], 'content': msg['content']} for msg in messages[-MAX_CONVERSATION_HISTORY * 2:]]


# ============================================
# 訊息記錄 (線程安全)
# ============================================

def log_message(direction: str, user_id: str, message: str, message_type: str = 'text'):
    """記錄訊息"""
    with message_lock:
        try:
            logs = []
            if os.path.exists(MESSAGES_LOG_FILE):
                with open(MESSAGES_LOG_FILE, 'r', encoding='utf-8') as f:
                    logs = json.load(f)

            logs.append({
                'direction': direction,  # 'incoming' 或 'outgoing'
                'user_id': user_id,
                'message': message,
                'message_type': message_type,
                'timestamp': datetime.now().isoformat()
            })

            # 只保留最近 1000 條記錄
            if len(logs) > 1000:
                logs = logs[-1000:]

            with open(MESSAGES_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"記錄訊息失敗: {e}")


# ============================================
# 關鍵字規則管理
# ============================================

def load_keyword_rules() -> list:
    """載入關鍵字規則"""
    try:
        if os.path.exists(KEYWORD_RULES_FILE):
            with open(KEYWORD_RULES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"載入關鍵字規則失敗: {e}")

    # 預設規則
    default_rules = [
        {
            'keywords': ['運費', '運費多少', '免運'],
            'reply': '親愛的顧客您好！運費依據您的收件地址計算，滿 $499 即享免運優惠喔！',
            'enabled': True
        },
        {
            'keywords': ['退貨', '退款', '換貨'],
            'reply': '親愛的顧客您好！我們提供 7 天鑑賞期，如需退換貨請保持商品完整，並聯繫我們客服處理。',
            'enabled': True
        },
        {
            'keywords': ['出貨', '什麼時候寄', '寄出'],
            'reply': '親愛的顧客您好！訂單確認後 1-2 個工作天內出貨，届時會有物流通知喔！',
            'enabled': True
        },
        {
            'keywords': ['營業時間', '客服時間'],
            'reply': '親愛的顧客您好！我們的客服時間為週一至週五 9:00-18:00，感謝您的耐心等候！',
            'enabled': True
        }
    ]

    # 儲存預設規則
    try:
        with open(KEYWORD_RULES_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_rules, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return default_rules


def check_keyword_reply(message: str) -> str | None:
    """檢查是否符合關鍵字規則"""
    if not ENABLE_KEYWORD_REPLY:
        return None

    rules = load_keyword_rules()
    message_lower = message.lower()

    for rule in rules:
        if not rule.get('enabled', True):
            continue

        for keyword in rule.get('keywords', []):
            if keyword.lower() in message_lower:
                logger.info(f"符合關鍵字規則: {keyword}")
                return rule.get('reply')

    return None


# ============================================
# 核心簽章演算法 (HMAC-SHA256)
# ============================================

def calculate_sign(path: str, timestamp: int, access_token: str = '', shop_id: int = 0) -> str:
    """
    計算蝦皮 API 簽章

    Base String 格式：partner_id + path + timestamp + access_token + shop_id
    若 API 不需要 access_token 或 shop_id，則不拼接入字串
    """
    base_string = f"{SHOPEE_PARTNER_ID}{path}{timestamp}"

    if access_token:
        base_string += access_token

    if shop_id:
        base_string += str(shop_id)

    sign = hmac.new(
        SHOPEE_PARTNER_KEY.encode('utf-8'),
        base_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return sign


# ============================================
# OAuth 2.0 授權模組
# ============================================

@app.route('/auth/login')
def auth_login():
    """GET /auth/login - 產生並重定向至蝦皮授權頁面"""
    try:
        timestamp = int(time.time())
        path = '/api/v2/shop/auth_partner'
        sign = calculate_sign(path, timestamp)

        auth_url = (
            f"{SHOPEE_API_BASE}{path}"
            f"?partner_id={SHOPEE_PARTNER_ID}"
            f"&timestamp={timestamp}"
            f"&sign={sign}"
            f"&redirect={REDIRECT_URL}"
        )

        logger.info(f"重定向至蝦皮授權頁面")
        return redirect(auth_url)

    except Exception as e:
        logger.error(f"授權登入失敗: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/auth/callback')
def auth_callback():
    """GET /auth/callback - 接收蝦皮回傳的 code 和 shop_id，交換 Token"""
    try:
        code = request.args.get('code')
        shop_id = request.args.get('shop_id')

        if not code or not shop_id:
            return "授權失敗：缺少必要參數", 400

        shop_id = int(shop_id)
        logger.info(f"收到授權回調 - code: {code}, shop_id: {shop_id}")

        timestamp = int(time.time())
        path = '/api/v2/auth/token/get'
        sign = calculate_sign(path, timestamp)

        url = f"{SHOPEE_API_BASE}{path}?partner_id={SHOPEE_PARTNER_ID}&timestamp={timestamp}&sign={sign}"

        payload = {
            'code': code,
            'partner_id': SHOPEE_PARTNER_ID,
            'shop_id': shop_id
        }

        response = requests.post(url, json=payload, timeout=30)
        result = response.json()

        if 'error' in result and result['error']:
            logger.error(f"Token 交換失敗: {result}")
            return f"授權失敗：{result.get('message', '未知錯誤')}", 400

        token_data = {
            'access_token': result.get('access_token'),
            'refresh_token': result.get('refresh_token'),
            'shop_id': shop_id,
            'expire_in': result.get('expire_in'),
            'created_at': timestamp,
            'expires_at': timestamp + result.get('expire_in', 0),
            'updated_at': datetime.now().isoformat()
        }

        write_tokens(token_data)
        logger.info(f"授權成功！Shop ID: {shop_id}")

        return render_template_string(SUCCESS_PAGE_TEMPLATE, shop_id=shop_id)

    except requests.exceptions.Timeout:
        logger.error("Token 交換請求超時")
        return "授權失敗：請求超時", 500
    except requests.exceptions.RequestException as e:
        logger.error(f"Token 交換請求失敗: {e}")
        return f"授權失敗：{str(e)}", 500
    except Exception as e:
        logger.error(f"授權回調處理失敗: {e}")
        return f"授權失敗：{str(e)}", 500


# ============================================
# Token 管理模組
# ============================================

def refresh_access_token() -> bool:
    """刷新 Access Token"""
    try:
        tokens = read_tokens()

        if not tokens or 'refresh_token' not in tokens:
            logger.warning("無可用的 Refresh Token，跳過刷新")
            return False

        refresh_token = tokens['refresh_token']
        shop_id = tokens.get('shop_id', 0)

        timestamp = int(time.time())
        path = '/api/v2/auth/access_token/get'
        sign = calculate_sign(path, timestamp)

        url = f"{SHOPEE_API_BASE}{path}?partner_id={SHOPEE_PARTNER_ID}&timestamp={timestamp}&sign={sign}"

        payload = {
            'refresh_token': refresh_token,
            'partner_id': SHOPEE_PARTNER_ID,
            'shop_id': shop_id
        }

        response = requests.post(url, json=payload, timeout=30)
        result = response.json()

        if 'error' in result and result['error']:
            logger.error(f"Token 刷新失敗: {result}")
            return False

        tokens['access_token'] = result.get('access_token')
        tokens['refresh_token'] = result.get('refresh_token')
        tokens['expire_in'] = result.get('expire_in')
        tokens['expires_at'] = timestamp + result.get('expire_in', 0)
        tokens['updated_at'] = datetime.now().isoformat()

        write_tokens(tokens)
        logger.info("Token 刷新成功！")
        return True

    except requests.exceptions.Timeout:
        logger.error("Token 刷新請求超時")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Token 刷新請求失敗: {e}")
        return False
    except Exception as e:
        logger.error(f"Token 刷新失敗: {e}")
        return False


@app.route('/auth/refresh', methods=['POST'])
def manual_refresh_token():
    """POST /auth/refresh - 手動刷新 Token"""
    try:
        success = refresh_access_token()
        if success:
            tokens = read_tokens()
            return jsonify({
                'success': True,
                'message': 'Token 刷新成功',
                'expires_at': tokens.get('expires_at'),
                'updated_at': tokens.get('updated_at')
            })
        else:
            return jsonify({'success': False, 'message': 'Token 刷新失敗'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# Webhook 簽章驗證
# ============================================

def verify_webhook_signature(authorization: str, body: bytes) -> bool:
    """驗證蝦皮 Webhook 簽章"""
    try:
        calculated_sign = hmac.new(
            SHOPEE_PARTNER_KEY.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(authorization, calculated_sign)
    except Exception as e:
        logger.error(f"Webhook 簽章驗證失敗: {e}")
        return False


# ============================================
# OpenAI 回覆生成
# ============================================

def generate_ai_response(user_id: str, message: str) -> str:
    """使用 OpenAI GPT-4o 生成回覆（含對話上下文）"""
    try:
        client = get_openai_client()
        if not client:
            return "抱歉，AI 服務暫時無法使用，請稍後再試。"

        # 取得對話歷史
        history = get_conversation_history(user_id)

        # 組合訊息
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=500,
            temperature=0.7
        )

        reply = response.choices[0].message.content
        logger.info(f"AI 回覆生成成功: {reply[:50]}...")
        return reply

    except Exception as e:
        logger.error(f"OpenAI API 呼叫失敗: {e}")
        return "抱歉，系統暫時無法回覆，請稍後再試或聯繫客服人員。"


# ============================================
# 蝦皮 API 呼叫
# ============================================

def send_shopee_message(to_user_id: str, message: str) -> bool:
    """發送文字訊息至蝦皮聊天"""
    try:
        tokens = read_tokens()

        if not tokens or 'access_token' not in tokens:
            logger.error("無可用的 Access Token")
            return False

        access_token = tokens['access_token']
        shop_id = tokens.get('shop_id', 0)

        timestamp = int(time.time())
        path = '/api/v2/sellerchat/send_message'
        sign = calculate_sign(path, timestamp, access_token, shop_id)

        url = (
            f"{SHOPEE_API_BASE}{path}"
            f"?partner_id={SHOPEE_PARTNER_ID}"
            f"&timestamp={timestamp}"
            f"&access_token={access_token}"
            f"&shop_id={shop_id}"
            f"&sign={sign}"
        )

        payload = {
            'to_id': int(to_user_id),
            'message_type': 'text',
            'content': {
                'text': message
            }
        }

        response = requests.post(url, json=payload, timeout=30)
        result = response.json()

        if 'error' in result and result['error']:
            logger.error(f"蝦皮訊息發送失敗: {result}")
            return False

        logger.info(f"訊息發送成功至用戶 {to_user_id}")
        return True

    except requests.exceptions.Timeout:
        logger.error("蝦皮訊息發送請求超時")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"蝦皮訊息發送請求失敗: {e}")
        return False
    except Exception as e:
        logger.error(f"蝦皮訊息發送失敗: {e}")
        return False


def get_conversation_list(page_size: int = 20, offset: str = '') -> dict:
    """取得聊天列表"""
    try:
        tokens = read_tokens()
        if not tokens or 'access_token' not in tokens:
            return {'error': '無可用的 Token'}

        access_token = tokens['access_token']
        shop_id = tokens.get('shop_id', 0)

        timestamp = int(time.time())
        path = '/api/v2/sellerchat/get_conversation_list'
        sign = calculate_sign(path, timestamp, access_token, shop_id)

        url = (
            f"{SHOPEE_API_BASE}{path}"
            f"?partner_id={SHOPEE_PARTNER_ID}"
            f"&timestamp={timestamp}"
            f"&access_token={access_token}"
            f"&shop_id={shop_id}"
            f"&sign={sign}"
        )

        payload = {
            'direction': 'latest',
            'type': 'all',
            'page_size': page_size
        }

        if offset:
            payload['offset'] = offset

        response = requests.post(url, json=payload, timeout=30)
        return response.json()

    except Exception as e:
        logger.error(f"取得聊天列表失敗: {e}")
        return {'error': str(e)}


def get_message_list(conversation_id: str, page_size: int = 20, offset: str = '') -> dict:
    """取得對話訊息列表"""
    try:
        tokens = read_tokens()
        if not tokens or 'access_token' not in tokens:
            return {'error': '無可用的 Token'}

        access_token = tokens['access_token']
        shop_id = tokens.get('shop_id', 0)

        timestamp = int(time.time())
        path = '/api/v2/sellerchat/get_message'
        sign = calculate_sign(path, timestamp, access_token, shop_id)

        url = (
            f"{SHOPEE_API_BASE}{path}"
            f"?partner_id={SHOPEE_PARTNER_ID}"
            f"&timestamp={timestamp}"
            f"&access_token={access_token}"
            f"&shop_id={shop_id}"
            f"&sign={sign}"
        )

        payload = {
            'conversation_id': int(conversation_id),
            'page_size': page_size
        }

        if offset:
            payload['offset'] = offset

        response = requests.post(url, json=payload, timeout=30)
        return response.json()

    except Exception as e:
        logger.error(f"取得對話訊息失敗: {e}")
        return {'error': str(e)}


# ============================================
# Webhook 端點
# ============================================

@app.route('/webhook', methods=['POST'])
@rate_limit
def webhook():
    """POST /webhook - 接收蝦皮推送的訊息並自動回覆"""
    try:
        raw_body = request.get_data()

        # 驗證 Webhook 簽章
        authorization = request.headers.get('Authorization', '')
        if authorization and not verify_webhook_signature(authorization, raw_body):
            logger.warning("Webhook 簽章驗證失敗，可能是偽造請求")
            return jsonify({'error': 'Invalid signature'}), 401

        data = request.get_json()
        logger.info(f"收到 Webhook: {json.dumps(data, ensure_ascii=False)[:200]}")

        # 檢查是否為新訊息 (code: 3)
        code = data.get('code')
        if code != 3:
            logger.info(f"非訊息類型 Webhook (code: {code})，略過")
            return jsonify({'status': 'ignored'}), 200

        # 解析訊息內容
        shop_id = data.get('shop_id')
        msg_data = data.get('data', {})
        conversation_id = msg_data.get('conversation_id')
        from_id = msg_data.get('from_id')
        to_id = msg_data.get('to_id')
        message_type = msg_data.get('message_type', 'text')

        # 如果是賣家自己發的訊息，不處理
        if from_id == shop_id:
            logger.info("這是賣家自己的訊息，不處理")
            return jsonify({'status': 'ignored'}), 200

        # 取得訊息內容
        content = msg_data.get('content', {})

        # 處理不同訊息類型
        if message_type == 'text':
            message_content = content.get('text', '')
        elif message_type == 'image':
            message_content = '[圖片訊息]'
        elif message_type == 'sticker':
            message_content = '[貼圖訊息]'
        elif message_type == 'order':
            message_content = '[訂單訊息]'
        elif message_type == 'item':
            message_content = '[商品訊息]'
        else:
            message_content = f'[{message_type}訊息]'

        if not conversation_id or not message_content:
            logger.warning("訊息缺少必要欄位")
            return jsonify({'status': 'missing_fields'}), 200

        user_id = str(from_id)
        logger.info(f"收到買家訊息 (type: {message_type}): {message_content}")

        # 記錄收到的訊息
        log_message('incoming', user_id, message_content, message_type)

        # 非文字訊息的處理
        if message_type != 'text':
            ai_reply = "收到您的訊息了！如有任何問題請直接輸入文字詢問，我會盡快為您解答。"
        else:
            # 先檢查關鍵字規則
            keyword_reply = check_keyword_reply(message_content)

            if keyword_reply:
                ai_reply = keyword_reply
            else:
                # 記錄用戶訊息到對話歷史
                add_to_conversation(user_id, 'user', message_content)

                # 生成 AI 回覆
                ai_reply = generate_ai_response(user_id, message_content)

                # 記錄 AI 回覆到對話歷史
                add_to_conversation(user_id, 'assistant', ai_reply)

        # 發送回覆
        send_result = send_shopee_message(user_id, ai_reply)

        # 記錄發送的訊息
        log_message('outgoing', user_id, ai_reply, 'text')

        if send_result:
            return jsonify({'status': 'success', 'reply': ai_reply}), 200
        else:
            return jsonify({'status': 'send_failed'}), 500

    except json.JSONDecodeError as e:
        logger.error(f"Webhook JSON 解析失敗: {e}")
        return jsonify({'error': 'Invalid JSON'}), 400
    except Exception as e:
        logger.error(f"Webhook 處理失敗: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================
# 測試端點
# ============================================

@app.route('/test/webhook', methods=['POST'])
def test_webhook():
    """POST /test/webhook - 測試 Webhook（模擬蝦皮推送）"""
    try:
        data = request.get_json()
        message = data.get('message', '這是測試訊息')
        user_id = data.get('user_id', '12345')

        # 先檢查關鍵字規則
        keyword_reply = check_keyword_reply(message)

        if keyword_reply:
            reply = keyword_reply
            reply_type = 'keyword'
        else:
            # 記錄用戶訊息
            add_to_conversation(user_id, 'user', message)

            # 生成 AI 回覆
            reply = generate_ai_response(user_id, message)
            reply_type = 'ai'

            # 記錄 AI 回覆
            add_to_conversation(user_id, 'assistant', reply)

        return jsonify({
            'status': 'success',
            'reply_type': reply_type,
            'reply': reply,
            'message': message,
            'user_id': user_id
        })

    except Exception as e:
        logger.error(f"測試 Webhook 失敗: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/test/send', methods=['POST'])
def test_send_message():
    """POST /test/send - 測試發送訊息"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        message = data.get('message')

        if not user_id or not message:
            return jsonify({'error': '缺少 user_id 或 message'}), 400

        result = send_shopee_message(str(user_id), message)

        return jsonify({
            'success': result,
            'user_id': user_id,
            'message': message
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================
# 管理端點
# ============================================

@app.route('/api/conversations')
def api_get_conversations():
    """GET /api/conversations - 取得蝦皮聊天列表"""
    try:
        page_size = request.args.get('page_size', 20, type=int)
        offset = request.args.get('offset', '')
        result = get_conversation_list(page_size, offset)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/messages/<conversation_id>')
def api_get_messages(conversation_id):
    """GET /api/messages/<id> - 取得對話訊息"""
    try:
        page_size = request.args.get('page_size', 20, type=int)
        offset = request.args.get('offset', '')
        result = get_message_list(conversation_id, page_size, offset)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs')
def api_get_message_logs():
    """GET /api/logs - 取得訊息記錄"""
    try:
        logs = []
        if os.path.exists(MESSAGES_LOG_FILE):
            with open(MESSAGES_LOG_FILE, 'r', encoding='utf-8') as f:
                logs = json.load(f)

        # 支援分頁
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        return jsonify({
            'total': len(logs),
            'logs': logs[-(offset + limit):][-limit:] if logs else []
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/keyword-rules', methods=['GET', 'POST'])
def api_keyword_rules():
    """管理關鍵字規則"""
    if request.method == 'GET':
        return jsonify(load_keyword_rules())

    elif request.method == 'POST':
        try:
            rules = request.get_json()
            with open(KEYWORD_RULES_FILE, 'w', encoding='utf-8') as f:
                json.dump(rules, f, ensure_ascii=False, indent=2)
            return jsonify({'success': True, 'message': '規則已更新'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/api/conversation-history/<user_id>')
def api_get_conversation_history(user_id):
    """GET /api/conversation-history/<user_id> - 取得用戶對話歷史"""
    try:
        conversations = read_conversations()
        if user_id in conversations:
            return jsonify(conversations[user_id])
        return jsonify({'messages': [], 'message': '無對話記錄'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/conversation-history/<user_id>', methods=['DELETE'])
def api_clear_conversation_history(user_id):
    """DELETE /api/conversation-history/<user_id> - 清除用戶對話歷史"""
    try:
        conversations = read_conversations()
        if user_id in conversations:
            del conversations[user_id]
            write_conversations(conversations)
            return jsonify({'success': True, 'message': '對話歷史已清除'})
        return jsonify({'success': True, 'message': '無對話記錄'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================
# 健康檢查與首頁
# ============================================

@app.route('/')
def index():
    """首頁"""
    return render_template_string(INDEX_PAGE_TEMPLATE)


@app.route('/status')
def status():
    """系統狀態檢查"""
    tokens = read_tokens()
    has_token = bool(tokens and tokens.get('access_token'))

    # 檢查 Token 是否即將過期
    token_status = 'no_token'
    if has_token:
        expires_at = tokens.get('expires_at', 0)
        current_time = int(time.time())
        if current_time >= expires_at:
            token_status = 'expired'
        elif expires_at - current_time < 3600:  # 1 小時內過期
            token_status = 'expiring_soon'
        else:
            token_status = 'valid'

    return jsonify({
        'status': 'running',
        'has_valid_token': has_token,
        'token_status': token_status,
        'shop_id': tokens.get('shop_id') if has_token else None,
        'token_expires_at': tokens.get('expires_at') if has_token else None,
        'token_updated_at': tokens.get('updated_at') if has_token else None,
        'features': {
            'keyword_reply': ENABLE_KEYWORD_REPLY,
            'conversation_history': MAX_CONVERSATION_HISTORY,
            'rate_limit': RATE_LIMIT_PER_MINUTE
        }
    })


@app.route('/dashboard')
def dashboard():
    """管理儀表板"""
    return render_template_string(DASHBOARD_PAGE_TEMPLATE)


# ============================================
# HTML 模板
# ============================================

SUCCESS_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>授權成功</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f5f5f5; }
        .container { background: white; padding: 40px; border-radius: 10px; max-width: 500px; margin: 0 auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .success { color: #28a745; font-size: 24px; }
        .info { color: #666; margin-top: 20px; }
        .shop-id { background: #e9ecef; padding: 10px; border-radius: 5px; margin: 20px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="success">✅ 蝦皮授權成功！</h1>
        <div class="shop-id">Shop ID: <strong>{{ shop_id }}</strong></div>
        <p class="info">您的商店已成功連接至 AI 客服系統。</p>
        <p class="info">系統將自動回覆客戶訊息。</p>
        <p class="info" style="margin-top: 30px;"><a href="/dashboard">前往管理儀表板</a></p>
    </div>
</body>
</html>
"""

INDEX_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>蝦皮 AI 客服系統</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }
        .container { max-width: 900px; margin: 0 auto; padding: 40px 20px; }
        .header { text-align: center; color: white; margin-bottom: 40px; }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header p { font-size: 1.2em; opacity: 0.9; }
        .card { background: white; border-radius: 15px; padding: 30px; margin-bottom: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); }
        .status-badge { display: inline-block; padding: 8px 16px; border-radius: 20px; font-weight: bold; }
        .status-running { background: #d4edda; color: #155724; }
        .endpoints { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin-top: 20px; }
        .endpoint { background: #f8f9fa; padding: 15px; border-radius: 10px; border-left: 4px solid #ee4d2d; }
        .endpoint strong { color: #ee4d2d; }
        .endpoint p { color: #666; margin-top: 5px; font-size: 0.9em; }
        .btn { display: inline-block; padding: 12px 30px; background: #ee4d2d; color: white; text-decoration: none; border-radius: 25px; font-weight: bold; transition: transform 0.2s; }
        .btn:hover { transform: scale(1.05); }
        .steps { counter-reset: step; }
        .steps li { list-style: none; padding: 15px 0 15px 50px; position: relative; border-left: 2px solid #eee; }
        .steps li:before { counter-increment: step; content: counter(step); position: absolute; left: -15px; width: 30px; height: 30px; background: #ee4d2d; color: white; border-radius: 50%; text-align: center; line-height: 30px; font-weight: bold; }
        .steps li:last-child { border-left: none; }
        code { background: #e9ecef; padding: 2px 8px; border-radius: 4px; font-family: 'Consolas', monospace; }
        .cta { text-align: center; margin-top: 30px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🛒 蝦皮 AI 自動客服系統</h1>
            <p>Shopee AI Auto Customer Service</p>
        </div>

        <div class="card">
            <h2>系統狀態</h2>
            <p style="margin-top: 15px;">
                <span class="status-badge status-running">✅ 系統運行中</span>
            </p>
            <div class="cta">
                <a href="/auth/login" class="btn">🔐 開始授權</a>
                <a href="/dashboard" class="btn" style="background: #6c757d; margin-left: 10px;">📊 管理儀表板</a>
            </div>
        </div>

        <div class="card">
            <h2>API 端點</h2>
            <div class="endpoints">
                <div class="endpoint">
                    <strong>GET /auth/login</strong>
                    <p>開始 OAuth 授權流程</p>
                </div>
                <div class="endpoint">
                    <strong>GET /auth/callback</strong>
                    <p>OAuth 回調端點</p>
                </div>
                <div class="endpoint">
                    <strong>POST /auth/refresh</strong>
                    <p>手動刷新 Token</p>
                </div>
                <div class="endpoint">
                    <strong>POST /webhook</strong>
                    <p>蝦皮訊息 Webhook</p>
                </div>
                <div class="endpoint">
                    <strong>GET /status</strong>
                    <p>系統狀態檢查</p>
                </div>
                <div class="endpoint">
                    <strong>GET /api/logs</strong>
                    <p>訊息記錄查詢</p>
                </div>
                <div class="endpoint">
                    <strong>POST /test/webhook</strong>
                    <p>測試 AI 回覆</p>
                </div>
                <div class="endpoint">
                    <strong>GET /api/keyword-rules</strong>
                    <p>關鍵字規則管理</p>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>使用步驟</h2>
            <ol class="steps">
                <li>設定 <code>.env</code> 環境變數（Partner ID、Key、OpenAI Key）</li>
                <li>使用 ngrok 建立外部連線：<code>ngrok http 5000</code></li>
                <li>點擊「開始授權」進行蝦皮 OAuth 授權</li>
                <li>在蝦皮開發者後台設定 Webhook URL</li>
                <li>系統自動回覆客戶訊息！</li>
            </ol>
        </div>
    </div>
</body>
</html>
"""

DASHBOARD_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>管理儀表板 - 蝦皮 AI 客服</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #f5f7fa; }
        .navbar { background: #ee4d2d; color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; }
        .navbar h1 { font-size: 1.3em; }
        .container { max-width: 1200px; margin: 0 auto; padding: 30px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .card { background: white; border-radius: 10px; padding: 25px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
        .card h3 { color: #333; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 2px solid #eee; }
        .stat { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f0f0f0; }
        .stat:last-child { border-bottom: none; }
        .stat-label { color: #666; }
        .stat-value { font-weight: bold; color: #333; }
        .status-valid { color: #28a745; }
        .status-expired { color: #dc3545; }
        .status-warning { color: #ffc107; }
        .btn { padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; transition: opacity 0.2s; }
        .btn:hover { opacity: 0.8; }
        .btn-primary { background: #ee4d2d; color: white; }
        .btn-secondary { background: #6c757d; color: white; }
        .test-form { margin-top: 15px; }
        .test-form input, .test-form textarea { width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 5px; }
        .test-form textarea { height: 80px; resize: vertical; }
        .response-box { background: #f8f9fa; padding: 15px; border-radius: 5px; margin-top: 15px; white-space: pre-wrap; font-family: monospace; font-size: 0.9em; max-height: 200px; overflow-y: auto; }
        .log-item { padding: 10px; border-bottom: 1px solid #eee; font-size: 0.9em; }
        .log-item:last-child { border-bottom: none; }
        .log-incoming { border-left: 3px solid #17a2b8; }
        .log-outgoing { border-left: 3px solid #28a745; }
        .log-time { color: #999; font-size: 0.8em; }
        .log-user { color: #ee4d2d; font-weight: bold; }
        .loading { text-align: center; padding: 20px; color: #666; }
    </style>
</head>
<body>
    <div class="navbar">
        <h1>🛒 蝦皮 AI 客服管理儀表板</h1>
        <a href="/" style="color: white; text-decoration: none;">← 返回首頁</a>
    </div>

    <div class="container">
        <div class="grid">
            <!-- 系統狀態 -->
            <div class="card">
                <h3>📊 系統狀態</h3>
                <div id="status-content">
                    <div class="loading">載入中...</div>
                </div>
                <button class="btn btn-primary" style="margin-top: 15px; width: 100%;" onclick="refreshToken()">🔄 刷新 Token</button>
            </div>

            <!-- 測試 AI 回覆 -->
            <div class="card">
                <h3>🤖 測試 AI 回覆</h3>
                <div class="test-form">
                    <input type="text" id="test-user-id" placeholder="用戶 ID (預設: 12345)" value="12345">
                    <textarea id="test-message" placeholder="輸入測試訊息..."></textarea>
                    <button class="btn btn-primary" onclick="testWebhook()">發送測試</button>
                </div>
                <div id="test-response" class="response-box" style="display: none;"></div>
            </div>

            <!-- 訊息記錄 -->
            <div class="card" style="grid-column: span 2;">
                <h3>📝 最近訊息記錄</h3>
                <div id="logs-content" style="max-height: 400px; overflow-y: auto;">
                    <div class="loading">載入中...</div>
                </div>
                <button class="btn btn-secondary" style="margin-top: 15px;" onclick="loadLogs()">🔄 重新載入</button>
            </div>
        </div>
    </div>

    <script>
        // 載入系統狀態
        async function loadStatus() {
            try {
                const response = await fetch('/status');
                const data = await response.json();

                let statusClass = 'status-valid';
                let statusText = '有效';
                if (data.token_status === 'expired') {
                    statusClass = 'status-expired';
                    statusText = '已過期';
                } else if (data.token_status === 'expiring_soon') {
                    statusClass = 'status-warning';
                    statusText = '即將過期';
                } else if (data.token_status === 'no_token') {
                    statusClass = 'status-expired';
                    statusText = '未授權';
                }

                document.getElementById('status-content').innerHTML = `
                    <div class="stat">
                        <span class="stat-label">系統狀態</span>
                        <span class="stat-value status-valid">運行中</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">Token 狀態</span>
                        <span class="stat-value ${statusClass}">${statusText}</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">Shop ID</span>
                        <span class="stat-value">${data.shop_id || '未設定'}</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">關鍵字回覆</span>
                        <span class="stat-value">${data.features?.keyword_reply ? '啟用' : '停用'}</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">對話歷史</span>
                        <span class="stat-value">保留 ${data.features?.conversation_history || 10} 則</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">最後更新</span>
                        <span class="stat-value">${data.token_updated_at ? new Date(data.token_updated_at).toLocaleString() : '-'}</span>
                    </div>
                `;
            } catch (e) {
                document.getElementById('status-content').innerHTML = '<p style="color: red;">載入失敗</p>';
            }
        }

        // 刷新 Token
        async function refreshToken() {
            try {
                const response = await fetch('/auth/refresh', { method: 'POST' });
                const data = await response.json();
                alert(data.message || data.error);
                loadStatus();
            } catch (e) {
                alert('刷新失敗: ' + e.message);
            }
        }

        // 測試 Webhook
        async function testWebhook() {
            const userId = document.getElementById('test-user-id').value || '12345';
            const message = document.getElementById('test-message').value;

            if (!message) {
                alert('請輸入測試訊息');
                return;
            }

            try {
                const response = await fetch('/test/webhook', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: userId, message: message })
                });
                const data = await response.json();

                const responseBox = document.getElementById('test-response');
                responseBox.style.display = 'block';
                responseBox.textContent = JSON.stringify(data, null, 2);
            } catch (e) {
                alert('測試失敗: ' + e.message);
            }
        }

        // 載入訊息記錄
        async function loadLogs() {
            try {
                const response = await fetch('/api/logs?limit=20');
                const data = await response.json();

                if (!data.logs || data.logs.length === 0) {
                    document.getElementById('logs-content').innerHTML = '<p style="color: #666; text-align: center;">暫無訊息記錄</p>';
                    return;
                }

                let html = '';
                data.logs.reverse().forEach(log => {
                    const dirClass = log.direction === 'incoming' ? 'log-incoming' : 'log-outgoing';
                    const dirText = log.direction === 'incoming' ? '← 收到' : '→ 發送';
                    html += `
                        <div class="log-item ${dirClass}">
                            <span class="log-time">${new Date(log.timestamp).toLocaleString()}</span>
                            <span class="log-user">[${log.user_id}]</span>
                            <strong>${dirText}</strong>: ${log.message}
                        </div>
                    `;
                });

                document.getElementById('logs-content').innerHTML = html;
            } catch (e) {
                document.getElementById('logs-content').innerHTML = '<p style="color: red;">載入失敗</p>';
            }
        }

        // 頁面載入時執行
        loadStatus();
        loadLogs();

        // 每 30 秒自動更新
        setInterval(loadStatus, 30000);
        setInterval(loadLogs, 30000);
    </script>
</body>
</html>
"""


# ============================================
# 主程式入口
# ============================================

if __name__ == '__main__':
    # 驗證必要環境變數
    if not SHOPEE_PARTNER_ID:
        logger.error("缺少環境變數: SHOPEE_PARTNER_ID")
        exit(1)

    if not SHOPEE_PARTNER_KEY:
        logger.error("缺少環境變數: SHOPEE_PARTNER_KEY")
        exit(1)

    if not OPENAI_API_KEY:
        logger.warning("缺少環境變數: OPENAI_API_KEY，AI 回覆功能將無法使用")

    logger.info("=" * 50)
    logger.info("蝦皮 AI 自動客服系統啟動中...")
    logger.info(f"Partner ID: {SHOPEE_PARTNER_ID}")
    logger.info(f"Port: {APP_PORT}")
    logger.info(f"關鍵字回覆: {'啟用' if ENABLE_KEYWORD_REPLY else '停用'}")
    logger.info(f"對話歷史: 保留 {MAX_CONVERSATION_HISTORY} 則")
    logger.info(f"速率限制: {RATE_LIMIT_PER_MINUTE}/分鐘")
    logger.info("=" * 50)

    # 初始化關鍵字規則檔案
    load_keyword_rules()

    # 設定 APScheduler 背景排程
    scheduler = BackgroundScheduler()

    # 每 3.5 小時刷新 Token
    scheduler.add_job(
        refresh_access_token,
        'interval',
        hours=3.5,
        id='token_refresh',
        name='Token 自動刷新'
    )

    scheduler.start()
    logger.info("Token 自動刷新排程已啟動（每 3.5 小時）")

    # 啟動 Flask 伺服器
    try:
        app.run(
            host='0.0.0.0',
            port=APP_PORT,
            debug=False,
            threaded=True
        )
    except KeyboardInterrupt:
        logger.info("系統關閉中...")
        scheduler.shutdown()
