#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服 - 簡易啟動腳本
會開啟瀏覽器，讓你登入蝦皮後自動監控聊天
"""

import asyncio
import os
import time
from pathlib import Path
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

# Gemini AI
import google.generativeai as genai

# Playwright
from playwright.async_api import async_playwright

# ============================================
# 設定
# ============================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SHOPEE_CHAT_URL = os.getenv("SHOPEE_CHAT_URL", "https://seller.shopee.tw/portal/chatroom")

# 知識庫
KNOWLEDGE_BASE = """
【商店資訊】
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

SYSTEM_PROMPT = """你是一位親切專業的電商客服人員。請用繁體中文回覆客戶問題。
回答要簡潔有禮貌，不超過100字。"""


# ============================================
# AI 回覆生成
# ============================================

def generate_ai_reply(customer_message: str) -> str:
    """使用 Gemini 生成回覆"""
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = f"""
{SYSTEM_PROMPT}

【知識庫參考資料】
{KNOWLEDGE_BASE}

【客戶問題】
{customer_message}

請根據以上資訊回覆客戶，回答要簡潔有禮貌。
"""
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"AI 回覆錯誤: {e}")
        return None


# ============================================
# 主程式
# ============================================

async def main():
    print("=" * 50)
    print("🤖 遇見未來 AI 客服系統")
    print("=" * 50)

    # 檢查 API Key
    if not GEMINI_API_KEY:
        print("❌ 錯誤：未設定 GEMINI_API_KEY")
        print("請在 .env 檔案中設定 Gemini API Key")
        return

    print(f"✅ Gemini API Key: {GEMINI_API_KEY[:8]}...")
    print(f"✅ 蝦皮聊天網址: {SHOPEE_CHAT_URL}")
    print()

    # 測試 AI 回覆
    print("📝 測試 AI 回覆...")
    test_reply = generate_ai_reply("運費多少？")
    if test_reply:
        print(f"✅ AI 測試成功: {test_reply[:50]}...")
    else:
        print("❌ AI 測試失敗")
        return

    print()
    print("🚀 正在啟動瀏覽器...")
    print("💡 請在瀏覽器中登入你的蝦皮賣家帳號")
    print()

    # 啟動瀏覽器
    async with async_playwright() as p:
        # 使用持久化的瀏覽器資料目錄（保留登入狀態）
        user_data_dir = Path("browser_data")
        user_data_dir.mkdir(exist_ok=True)

        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,  # 顯示瀏覽器視窗
            viewport={"width": 1280, "height": 800}
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # 前往蝦皮聊天頁面
        print(f"📱 正在前往: {SHOPEE_CHAT_URL}")
        await page.goto(SHOPEE_CHAT_URL)

        print()
        print("=" * 50)
        print("✅ 瀏覽器已開啟！")
        print()
        print("📋 操作說明：")
        print("   1. 在瀏覽器中登入你的蝦皮賣家帳號")
        print("   2. 登入後，程式會自動保存登入狀態")
        print("   3. 下次啟動時不需要重新登入")
        print()
        print("⚠️  目前為手動模式，你可以在瀏覽器中手動回覆")
        print("    AI 自動回覆功能需要進一步設定蝦皮頁面選擇器")
        print()
        print("按 Ctrl+C 結束程式...")
        print("=" * 50)

        # 保持瀏覽器開啟
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 程式已結束")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
