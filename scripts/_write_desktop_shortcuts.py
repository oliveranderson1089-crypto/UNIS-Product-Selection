"""One-off: write Windows .bat shortcuts to the user's Desktop.

cmd.exe parses .bat as ANSI (GBK on zh-CN) and requires CRLF line endings.
LF-only files or UTF-8-with-BOM trip silent parser exits — that's the
common "double-click does nothing" symptom.
"""
from pathlib import Path

START_BAT = r"""@echo off
title UNIS 产品选型 - Web UI

echo ========================================
echo   UNIS 产品选型 - Web UI
echo ========================================
echo.
echo 启动中,Gradio 起来后会自动打开浏览器...
echo 关闭方法: 在本窗口按 Ctrl+C 然后关闭窗口
echo.

cd /d "D:\Project\claude\UNIS-Product-Selection"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 虚拟环境不存在: .venv\Scripts\python.exe
    pause
    exit /b 1
)

REM Gradio 内置 inbrowser=True,服务就绪时自己开浏览器
".venv\Scripts\python.exe" -m src.cli ui

echo.
echo UI 已停止
pause
"""

STOP_BAT = r"""@echo off
title 停止 UNIS UI

echo.
echo 查找占用 7860 端口的进程...
echo.

powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 7860 -ErrorAction SilentlyContinue; if (-not $c) { Write-Host '没有运行中的 UI 服务' -ForegroundColor Yellow } else { $c | ForEach-Object { $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; if ($p) { Write-Host ('停止: PID=' + $p.Id) -ForegroundColor Cyan; Stop-Process -Id $p.Id -Force } } }"

echo.
echo 完成
timeout /t 3 /nobreak > nul
"""

DESK = Path(r"D:\ComputerFiles\Desktop")

for name, body in [("启动UI.bat", START_BAT), ("停止UI.bat", STOP_BAT)]:
    p = DESK / name
    # Critical: cmd.exe needs CRLF line endings + ANSI/GBK encoding
    p.write_bytes(body.replace("\n", "\r\n").encode("gbk"))
    print(f"wrote {p}  ({p.stat().st_size} bytes)")

# Sanity check
b = (DESK / "启动UI.bat").read_bytes()
print(f"first bytes look right: {b[:20]!r}")
print(f"has CRLF: {b'\\r\\n' in b}")
