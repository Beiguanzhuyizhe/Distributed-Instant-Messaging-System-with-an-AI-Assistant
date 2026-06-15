@echo off
echo ===== Restart Chat System =====

:: 1. Kill old server on port 8888
echo [1/4] Cleaning old server...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8888"') do (
    if not "%%a"=="" taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

:: 2. Keep database for reconnect/history demo
echo [2/4] Keeping existing database...
echo INFO: server\data\chat.db is preserved so groups and chat history survive server restart.

:: 3. Verify port is free
echo [3/4] Checking port...
python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',8888));s.close()"
if errorlevel 1 (
    echo ERROR: Port 8888 still in use. Close other programs and retry.
    pause
    exit /b 1
)

:: 4. Start server (AI key is read from environment variables)
echo [4/4] Starting server...
if "%BIGMODEL_API_KEY%"=="" if "%DASHSCOPE_API_KEY%"=="" (
    echo INFO: BIGMODEL_API_KEY or DASHSCOPE_API_KEY is not set. AI feature will be disabled.
)
start "ChatServer" cmd /c "python -m server.main & pause"

echo.
echo Done! Server started on port 8888.
echo.
echo Now open a client:
echo   python -m client.main --cli    (text mode)
echo   python -m client.main --gui    (graphic mode)
echo.
echo Test accounts: alice/pass123  bob/pass456
echo.
pause
