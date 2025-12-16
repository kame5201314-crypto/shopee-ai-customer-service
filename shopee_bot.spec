# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包設定檔
蝦皮 AI 客服機器人

使用方式:
    pyinstaller shopee_bot.spec

注意: 必須先執行 playwright install chromium 安裝瀏覽器
"""

import os
import sys
from pathlib import Path

# 取得 Playwright 瀏覽器路徑
def get_playwright_browser_path():
    """取得 Playwright Chromium 瀏覽器路徑"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", "from playwright._impl._driver import compute_driver_executable; print(compute_driver_executable())"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        driver_path = Path(result.stdout.strip())
        # Playwright 瀏覽器通常在 driver 同目錄的 package/.local-browsers
        browsers_path = driver_path.parent / "package" / ".local-browsers"
        if browsers_path.exists():
            return str(browsers_path)

    # 備用: 常見路徑
    home = Path.home()
    possible_paths = [
        home / ".cache" / "ms-playwright",
        home / "AppData" / "Local" / "ms-playwright",
        Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")),
    ]

    for path in possible_paths:
        if path.exists():
            return str(path)

    return None

# 分析設定
block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 包含專案檔案
        ('gemini_service.py', '.'),
        ('knowledge_loader.py', '.'),
        ('shopee_bot.py', '.'),

        # 知識庫範例
        ('knowledge_base', 'knowledge_base'),

        # CustomTkinter 資源 (重要!)
        # 需要手動找到 customtkinter 的安裝路徑
    ],
    hiddenimports=[
        # CustomTkinter
        'customtkinter',
        'darkdetect',
        'PIL',
        'PIL._tkinter_finder',

        # Playwright
        'playwright',
        'playwright.async_api',
        'playwright.sync_api',
        'playwright._impl',
        'playwright._impl._driver',

        # Google Generative AI
        'google.generativeai',
        'google.ai.generativelanguage',
        'google.api_core',
        'google.auth',
        'google.protobuf',

        # 資料處理
        'pandas',
        'openpyxl',
        'PyPDF2',

        # 網路請求
        'requests',
        'urllib3',
        'certifi',

        # 其他
        'fake_useragent',
        'dotenv',
        'python-dotenv',
        'asyncio',
        'queue',
        'threading',
        'json',
        'logging',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'scipy',
        'tkinter.test',
        'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='蝦皮AI客服機器人',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI 模式，不顯示命令列視窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if Path('icon.ico').exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='蝦皮AI客服機器人',
)
