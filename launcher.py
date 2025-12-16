#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服機器人 - GUI 啟動器
使用 CustomTkinter 打造現代化介面
適合非技術背景用戶使用
"""

import os
import sys
import json
import threading
import queue
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox

# CustomTkinter 現代化介面
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
except ImportError:
    print("請安裝 customtkinter: pip install customtkinter")
    sys.exit(1)

# 專案模組
from dotenv import load_dotenv

# ============================================
# 常數設定
# ============================================

APP_NAME = "蝦皮 AI 客服機器人"
APP_VERSION = "1.0.0"
CONFIG_FILE = "bot_config.json"
LOG_MAX_LINES = 500

# Vercel API 基礎 URL (需要用戶設定)
DEFAULT_VERCEL_URL = ""


# ============================================
# 日誌佇列 (線程安全)
# ============================================

log_queue = queue.Queue()


def log_message(message: str, level: str = "INFO"):
    """添加日誌訊息到佇列"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_queue.put(f"[{timestamp}] [{level}] {message}")


# ============================================
# 設定管理
# ============================================

class ConfigManager:
    """設定管理器"""

    def __init__(self):
        self.config_path = Path(CONFIG_FILE)
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """載入設定"""
        default_config = {
            "env_file_path": "",
            "vercel_api_url": "",
            "gemini_api_key": "",
            "auto_sync_knowledge": True,
            "last_run": None
        }

        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    default_config.update(saved)
            except:
                pass

        return default_config

    def save(self):
        """儲存設定"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_message(f"儲存設定失敗: {e}", "ERROR")

    def get(self, key: str, default=None):
        return self.config.get(key, default)

    def set(self, key: str, value):
        self.config[key] = value
        self.save()


# ============================================
# 知識庫同步器
# ============================================

class KnowledgeSyncer:
    """從 Vercel API 同步知識庫到本地"""

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')
        self.local_cache_dir = Path("knowledge_cache")
        self.local_cache_dir.mkdir(exist_ok=True)

    def sync(self) -> bool:
        """同步知識庫"""
        if not self.api_url:
            log_message("未設定 Vercel API URL，跳過同步", "WARN")
            return False

        try:
            import requests

            log_message("正在從雲端同步知識庫...")

            # 取得知識庫狀態
            status_url = f"{self.api_url}/api/knowledge-base/status"
            response = requests.get(status_url, timeout=30)

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    files_count = data.get("files_count", 0)
                    total_chars = data.get("total_chars", 0)
                    log_message(f"雲端知識庫: {files_count} 個檔案, {total_chars} 字元")

                    # 儲存知識庫內容到本地
                    self._save_knowledge_cache(data)
                    log_message("知識庫同步完成！", "SUCCESS")
                    return True
                else:
                    log_message(f"同步失敗: {data.get('error', '未知錯誤')}", "ERROR")
            else:
                log_message(f"API 請求失敗: HTTP {response.status_code}", "ERROR")

            return False

        except requests.exceptions.ConnectionError:
            log_message("無法連接到 Vercel 伺服器，請檢查網路連線", "ERROR")
            return False
        except Exception as e:
            log_message(f"同步知識庫時發生錯誤: {e}", "ERROR")
            return False

    def _save_knowledge_cache(self, data: dict):
        """儲存知識庫快取"""
        cache_file = self.local_cache_dir / "knowledge_status.json"
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================
# 機器人控制器
# ============================================

class BotController:
    """機器人控制器"""

    def __init__(self, on_log: callable):
        self.on_log = on_log
        self.is_running = False
        self.bot_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

    def start(self, env_path: str):
        """啟動機器人"""
        if self.is_running:
            log_message("機器人已經在運行中", "WARN")
            return

        # 載入環境變數
        if env_path and Path(env_path).exists():
            load_dotenv(env_path, override=True)
            log_message(f"已載入設定檔: {Path(env_path).name}")
        else:
            log_message("未找到設定檔，使用預設設定", "WARN")

        # 檢查必要的環境變數
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            log_message("錯誤: 未設定 GEMINI_API_KEY", "ERROR")
            return

        log_message(f"API Key: {api_key[:8]}...{api_key[-4:]}")

        # 啟動機器人線程
        self.stop_event.clear()
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()
        self.is_running = True
        log_message("機器人啟動中...", "SUCCESS")

    def stop(self):
        """停止機器人"""
        if not self.is_running:
            return

        log_message("正在停止機器人...")
        self.stop_event.set()
        self.is_running = False

        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=5)

        log_message("機器人已停止", "SUCCESS")

    def _run_bot(self):
        """機器人主邏輯 (在獨立線程中運行)"""
        try:
            # 動態導入機器人模組
            from shopee_bot import ShopeeBot

            log_message("正在初始化瀏覽器...")

            # 創建事件循環
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                bot = ShopeeBot()
                loop.run_until_complete(bot.run(self.stop_event))
            finally:
                loop.close()

        except ImportError:
            log_message("找不到機器人模組 (shopee_bot.py)，請確認檔案存在", "ERROR")
        except Exception as e:
            log_message(f"機器人執行錯誤: {e}", "ERROR")
        finally:
            self.is_running = False
            log_message("機器人線程已結束")


# ============================================
# GUI 主介面
# ============================================

class LauncherApp(ctk.CTk):
    """啟動器主視窗"""

    def __init__(self):
        super().__init__()

        # 視窗設定
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("800x600")
        self.minsize(700, 500)

        # 設定圖示 (如果存在)
        icon_path = Path("icon.ico")
        if icon_path.exists():
            self.iconbitmap(str(icon_path))

        # 初始化元件
        self.config_manager = ConfigManager()
        self.bot_controller = BotController(on_log=self.add_log)
        self.knowledge_syncer = None

        # 建立介面
        self._create_widgets()

        # 啟動日誌更新
        self._update_logs()

        # 載入之前的設定
        self._load_saved_settings()

    def _create_widgets(self):
        """建立介面元件"""

        # ===== 頂部標題區 =====
        header_frame = ctk.CTkFrame(self, fg_color="#ee4d2d", corner_radius=0)
        header_frame.pack(fill="x", padx=0, pady=0)

        title_label = ctk.CTkLabel(
            header_frame,
            text=f"🤖 {APP_NAME}",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="white"
        )
        title_label.pack(pady=20)

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="Shopee AI Customer Service Bot",
            font=ctk.CTkFont(size=14),
            text_color="white"
        )
        subtitle_label.pack(pady=(0, 15))

        # ===== 主內容區 =====
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # ----- 設定區 -----
        settings_frame = ctk.CTkFrame(main_frame)
        settings_frame.pack(fill="x", pady=(0, 15))

        settings_title = ctk.CTkLabel(
            settings_frame,
            text="⚙️ 設定",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        settings_title.pack(anchor="w", padx=15, pady=(15, 10))

        # 設定檔路徑
        env_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        env_frame.pack(fill="x", padx=15, pady=5)

        env_label = ctk.CTkLabel(env_frame, text="設定檔 (.env):", width=100, anchor="w")
        env_label.pack(side="left")

        self.env_path_var = ctk.StringVar()
        self.env_entry = ctk.CTkEntry(env_frame, textvariable=self.env_path_var, width=400)
        self.env_entry.pack(side="left", padx=10)

        self.env_browse_btn = ctk.CTkButton(
            env_frame,
            text="瀏覽...",
            width=80,
            command=self._browse_env_file
        )
        self.env_browse_btn.pack(side="left")

        # Vercel API URL
        api_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        api_frame.pack(fill="x", padx=15, pady=5)

        api_label = ctk.CTkLabel(api_frame, text="Vercel URL:", width=100, anchor="w")
        api_label.pack(side="left")

        self.api_url_var = ctk.StringVar()
        self.api_entry = ctk.CTkEntry(api_frame, textvariable=self.api_url_var, width=400, placeholder_text="https://your-app.vercel.app")
        self.api_entry.pack(side="left", padx=10)

        self.sync_btn = ctk.CTkButton(
            api_frame,
            text="同步知識庫",
            width=100,
            fg_color="#10b981",
            hover_color="#059669",
            command=self._sync_knowledge
        )
        self.sync_btn.pack(side="left")

        # 選項
        options_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        options_frame.pack(fill="x", padx=15, pady=(10, 15))

        self.auto_sync_var = ctk.BooleanVar(value=True)
        auto_sync_check = ctk.CTkCheckBox(
            options_frame,
            text="啟動時自動同步知識庫",
            variable=self.auto_sync_var
        )
        auto_sync_check.pack(side="left")

        # ----- 控制區 -----
        control_frame = ctk.CTkFrame(main_frame)
        control_frame.pack(fill="x", pady=(0, 15))

        control_inner = ctk.CTkFrame(control_frame, fg_color="transparent")
        control_inner.pack(pady=15)

        self.start_btn = ctk.CTkButton(
            control_inner,
            text="▶️ 啟動機器人",
            width=180,
            height=50,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#ee4d2d",
            hover_color="#d63a1e",
            command=self._toggle_bot
        )
        self.start_btn.pack(side="left", padx=10)

        self.status_label = ctk.CTkLabel(
            control_inner,
            text="● 已停止",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        )
        self.status_label.pack(side="left", padx=20)

        # ----- 日誌區 -----
        log_frame = ctk.CTkFrame(main_frame)
        log_frame.pack(fill="both", expand=True)

        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=15, pady=(10, 5))

        log_title = ctk.CTkLabel(
            log_header,
            text="📋 運作日誌",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        log_title.pack(side="left")

        clear_btn = ctk.CTkButton(
            log_header,
            text="清除",
            width=60,
            height=28,
            fg_color="gray",
            command=self._clear_logs
        )
        clear_btn.pack(side="right")

        self.log_text = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word"
        )
        self.log_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # 歡迎訊息
        self.add_log(f"歡迎使用 {APP_NAME} v{APP_VERSION}")
        self.add_log("請先匯入設定檔 (.env)，然後點擊「啟動機器人」")

    def _load_saved_settings(self):
        """載入已儲存的設定"""
        env_path = self.config_manager.get("env_file_path", "")
        if env_path:
            self.env_path_var.set(env_path)

        api_url = self.config_manager.get("vercel_api_url", "")
        if api_url:
            self.api_url_var.set(api_url)

        auto_sync = self.config_manager.get("auto_sync_knowledge", True)
        self.auto_sync_var.set(auto_sync)

    def _browse_env_file(self):
        """瀏覽選擇 .env 檔案"""
        file_path = filedialog.askopenfilename(
            title="選擇設定檔",
            filetypes=[
                ("環境變數檔", "*.env"),
                ("所有檔案", "*.*")
            ]
        )

        if file_path:
            self.env_path_var.set(file_path)
            self.config_manager.set("env_file_path", file_path)
            self.add_log(f"已選擇設定檔: {Path(file_path).name}")

    def _sync_knowledge(self):
        """同步知識庫"""
        api_url = self.api_url_var.get().strip()

        if not api_url:
            messagebox.showwarning("提示", "請先輸入 Vercel API URL")
            return

        # 儲存設定
        self.config_manager.set("vercel_api_url", api_url)

        # 在背景執行同步
        self.sync_btn.configure(state="disabled", text="同步中...")

        def sync_thread():
            syncer = KnowledgeSyncer(api_url)
            success = syncer.sync()

            # 回到主線程更新 UI
            self.after(0, lambda: self._on_sync_complete(success))

        threading.Thread(target=sync_thread, daemon=True).start()

    def _on_sync_complete(self, success: bool):
        """同步完成回調"""
        self.sync_btn.configure(state="normal", text="同步知識庫")

        if success:
            messagebox.showinfo("成功", "知識庫同步完成！")
        else:
            messagebox.showerror("錯誤", "知識庫同步失敗，請檢查日誌")

    def _toggle_bot(self):
        """切換機器人狀態"""
        if self.bot_controller.is_running:
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        """啟動機器人"""
        env_path = self.env_path_var.get().strip()
        api_url = self.api_url_var.get().strip()

        # 儲存設定
        self.config_manager.set("env_file_path", env_path)
        self.config_manager.set("vercel_api_url", api_url)
        self.config_manager.set("auto_sync_knowledge", self.auto_sync_var.get())
        self.config_manager.set("last_run", datetime.now().isoformat())

        # 自動同步知識庫
        if self.auto_sync_var.get() and api_url:
            self.add_log("啟動前同步知識庫...")
            syncer = KnowledgeSyncer(api_url)
            syncer.sync()

        # 啟動機器人
        self.bot_controller.start(env_path)

        # 更新 UI
        self.start_btn.configure(text="⏹️ 停止機器人", fg_color="#dc2626", hover_color="#b91c1c")
        self.status_label.configure(text="● 運行中", text_color="#10b981")
        self.env_browse_btn.configure(state="disabled")
        self.sync_btn.configure(state="disabled")

    def _stop_bot(self):
        """停止機器人"""
        self.bot_controller.stop()

        # 更新 UI
        self.start_btn.configure(text="▶️ 啟動機器人", fg_color="#ee4d2d", hover_color="#d63a1e")
        self.status_label.configure(text="● 已停止", text_color="gray")
        self.env_browse_btn.configure(state="normal")
        self.sync_btn.configure(state="normal")

    def add_log(self, message: str):
        """添加日誌"""
        log_queue.put(message)

    def _update_logs(self):
        """更新日誌顯示 (定時任務)"""
        try:
            while True:
                message = log_queue.get_nowait()
                self.log_text.insert("end", message + "\n")
                self.log_text.see("end")

                # 限制日誌行數
                lines = int(self.log_text.index('end-1c').split('.')[0])
                if lines > LOG_MAX_LINES:
                    self.log_text.delete("1.0", "2.0")
        except queue.Empty:
            pass

        # 每 100ms 更新一次
        self.after(100, self._update_logs)

    def _clear_logs(self):
        """清除日誌"""
        self.log_text.delete("1.0", "end")
        self.add_log("日誌已清除")

    def on_closing(self):
        """關閉視窗時"""
        if self.bot_controller.is_running:
            if messagebox.askyesno("確認", "機器人正在運行中，確定要關閉嗎？"):
                self.bot_controller.stop()
                self.destroy()
        else:
            self.destroy()


# ============================================
# 主程式入口
# ============================================

def main():
    """主程式"""
    app = LauncherApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    main()
