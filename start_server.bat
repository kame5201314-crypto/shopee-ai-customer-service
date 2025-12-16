@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ========================================
echo   蝦皮 AI 客服系統 - 安全版啟動器
echo ========================================
echo.
echo 正在安裝套件...
"C:\Users\kawei\AppData\Local\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt
echo.
echo 正在啟動伺服器...
"C:\Users\kawei\AppData\Local\Programs\Python\Python312\python.exe" server_secure.py
pause
