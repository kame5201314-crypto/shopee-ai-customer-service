#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮 AI 客服機器人 - 自動打包腳本

此腳本會自動:
1. 安裝所有依賴
2. 安裝 Playwright 瀏覽器
3. 使用 PyInstaller 打包成 EXE
4. 複製必要檔案到輸出目錄
5. 建立可發布的 ZIP 檔案

使用方式:
    python build.py

輸出:
    dist/蝦皮AI客服機器人/
    dist/蝦皮AI客服機器人.zip
"""

import os
import sys
import shutil
import subprocess
import zipfile
from pathlib import Path
from datetime import datetime

# ============================================
# 設定
# ============================================

APP_NAME = "蝦皮AI客服機器人"
VERSION = "1.0.0"
MAIN_SCRIPT = "launcher.py"

# 需要複製到輸出目錄的檔案
INCLUDE_FILES = [
    "gemini_service.py",
    "knowledge_loader.py",
    "shopee_bot.py",
    ".env.example",
]

# 需要複製的資料夾
INCLUDE_DIRS = [
    "knowledge_base",
]

# Playwright 瀏覽器路徑
PLAYWRIGHT_BROWSERS = None


# ============================================
# 輔助函式
# ============================================

def run_command(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    """執行命令"""
    print(f"執行: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=False)


def print_step(step: int, total: int, message: str):
    """印出步驟"""
    print(f"\n{'='*60}")
    print(f"[{step}/{total}] {message}")
    print('='*60)


def get_playwright_path() -> Path:
    """取得 Playwright 瀏覽器路徑"""
    # Windows 路徑
    home = Path.home()
    possible_paths = [
        home / "AppData" / "Local" / "ms-playwright",
        home / ".cache" / "ms-playwright",
    ]

    # 檢查環境變數
    env_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if env_path:
        possible_paths.insert(0, Path(env_path))

    for path in possible_paths:
        if path.exists():
            return path

    return None


def find_customtkinter_path() -> Path:
    """找到 CustomTkinter 安裝路徑"""
    try:
        import customtkinter
        return Path(customtkinter.__file__).parent
    except ImportError:
        return None


# ============================================
# 主要打包流程
# ============================================

def main():
    """主程式"""
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║        🤖 蝦皮 AI 客服機器人 - 自動打包腳本                  ║
║                                                              ║
║        版本: {VERSION}                                          ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)

    total_steps = 6
    project_dir = Path(__file__).parent
    dist_dir = project_dir / "dist"
    output_dir = dist_dir / APP_NAME

    # ===== Step 1: 安裝依賴 =====
    print_step(1, total_steps, "安裝 Python 依賴套件")

    run_command([
        sys.executable, "-m", "pip", "install", "-r", "requirements_build.txt"
    ])

    # ===== Step 2: 安裝 Playwright 瀏覽器 =====
    print_step(2, total_steps, "安裝 Playwright Chromium 瀏覽器")

    run_command([
        sys.executable, "-m", "playwright", "install", "chromium"
    ])

    playwright_path = get_playwright_path()
    if playwright_path:
        print(f"Playwright 瀏覽器路徑: {playwright_path}")
    else:
        print("警告: 找不到 Playwright 瀏覽器路徑，請手動複製")

    # ===== Step 3: 準備 PyInstaller 資源 =====
    print_step(3, total_steps, "準備打包資源")

    # 找到 CustomTkinter 路徑
    ctk_path = find_customtkinter_path()
    if ctk_path:
        print(f"CustomTkinter 路徑: {ctk_path}")
    else:
        print("警告: 找不到 CustomTkinter，請確認已安裝")

    # ===== Step 4: 執行 PyInstaller =====
    print_step(4, total_steps, "使用 PyInstaller 打包")

    # 建立 PyInstaller 命令
    pyinstaller_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",  # GUI 模式
        "--noconfirm",  # 覆蓋舊的輸出
        "--clean",  # 清理暫存
    ]

    # 添加圖示 (如果存在)
    icon_path = project_dir / "icon.ico"
    if icon_path.exists():
        pyinstaller_cmd.extend(["--icon", str(icon_path)])

    # 添加隱藏導入
    hidden_imports = [
        "customtkinter",
        "darkdetect",
        "PIL._tkinter_finder",
        "google.generativeai",
        "google.ai.generativelanguage",
        "google.api_core",
        "google.auth",
        "playwright",
        "playwright.async_api",
        "fake_useragent",
        "pandas",
        "openpyxl",
        "PyPDF2",
        "requests",
    ]

    for imp in hidden_imports:
        pyinstaller_cmd.extend(["--hidden-import", imp])

    # 添加資料檔案
    if ctk_path:
        pyinstaller_cmd.extend([
            "--add-data", f"{ctk_path};customtkinter"
        ])

    # 添加專案檔案
    for file in INCLUDE_FILES:
        file_path = project_dir / file
        if file_path.exists():
            pyinstaller_cmd.extend([
                "--add-data", f"{file_path};."
            ])

    # 添加資料夾
    for dir_name in INCLUDE_DIRS:
        dir_path = project_dir / dir_name
        if dir_path.exists():
            pyinstaller_cmd.extend([
                "--add-data", f"{dir_path};{dir_name}"
            ])

    # 主程式
    pyinstaller_cmd.append(MAIN_SCRIPT)

    run_command(pyinstaller_cmd)

    # ===== Step 5: 複製 Playwright 瀏覽器 =====
    print_step(5, total_steps, "複製 Playwright 瀏覽器到輸出目錄")

    if playwright_path and output_dir.exists():
        # 複製 Chromium
        chromium_dirs = list(playwright_path.glob("chromium-*"))
        if chromium_dirs:
            chromium_src = chromium_dirs[0]
            chromium_dst = output_dir / "playwright-browsers" / chromium_src.name

            if not chromium_dst.exists():
                print(f"複製 Chromium: {chromium_src.name}")
                shutil.copytree(chromium_src, chromium_dst)
            else:
                print("Chromium 已存在，跳過複製")

        # 建立環境變數設定
        env_setup = output_dir / "_internal" / "set_playwright_path.py"
        env_setup.parent.mkdir(parents=True, exist_ok=True)
        with open(env_setup, 'w', encoding='utf-8') as f:
            f.write('''
import os
import sys
from pathlib import Path

# 設定 Playwright 瀏覽器路徑
app_dir = Path(sys.executable).parent
browsers_path = app_dir / "playwright-browsers"
if browsers_path.exists():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
''')

    # ===== Step 6: 建立 ZIP 發布檔 =====
    print_step(6, total_steps, "建立 ZIP 發布檔")

    if output_dir.exists():
        zip_name = f"{APP_NAME}_v{VERSION}_{datetime.now().strftime('%Y%m%d')}.zip"
        zip_path = dist_dir / zip_name

        print(f"建立 ZIP: {zip_name}")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(output_dir)
                    zipf.write(file_path, arcname)

        print(f"ZIP 檔案大小: {zip_path.stat().st_size / 1024 / 1024:.1f} MB")

    # ===== 完成 =====
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║                     ✅ 打包完成!                              ║
║                                                              ║
║   輸出目錄: dist/{APP_NAME}/                      ║
║                                                              ║
║   主程式: {APP_NAME}.exe                          ║
║                                                              ║
║   發布 ZIP: dist/{APP_NAME}_v{VERSION}_*.zip              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

⚠️  重要提醒:
1. 首次執行需要手動登入蝦皮賣家帳號
2. 登入資訊會保存在 browser_data/ 資料夾中
3. 請妥善保管此資料夾，避免需要重新登入

📦 發布方式:
1. 將 dist/{APP_NAME}/ 整個資料夾壓縮
2. 或直接使用已生成的 ZIP 檔案
3. 客戶解壓縮後雙擊 {APP_NAME}.exe 即可使用
    """)


# ============================================
# 建立打包專用的 requirements
# ============================================

def create_build_requirements():
    """建立打包專用的 requirements 檔案"""
    requirements = """# 打包專用依賴
# 執行 python build.py 時會自動安裝

# 打包工具
pyinstaller>=6.0.0

# GUI 框架
customtkinter>=5.2.0
darkdetect>=0.8.0
Pillow>=10.0.0

# 瀏覽器自動化
playwright>=1.40.0
fake-useragent>=1.4.0

# AI 服務
google-generativeai>=0.8.0

# 資料處理
pandas>=2.0.0
openpyxl>=3.1.0
PyPDF2>=3.0.0

# 網路請求
requests>=2.31.0

# 環境變數
python-dotenv>=1.0.0

# Web 框架 (用於知識庫同步)
# fastapi>=0.104.0
# uvicorn>=0.24.0
"""

    with open("requirements_build.txt", 'w', encoding='utf-8') as f:
        f.write(requirements)

    print("已建立 requirements_build.txt")


if __name__ == "__main__":
    # 確保 requirements_build.txt 存在
    if not Path("requirements_build.txt").exists():
        create_build_requirements()

    main()
