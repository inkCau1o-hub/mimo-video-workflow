@echo off
chcp 65001 >nul
title 短视频文案生成工具 - 启动器

echo ========================================
echo    短视频文案生成工具
echo    正在检查运行环境...
echo ========================================
echo.

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo [1/4] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python环境
    echo.
    echo 请先安装Python 3.8或更高版本
    echo 下载地址: https://www.python.org/downloads/
    echo.
    echo 安装时请勾选 "Add Python to PATH"
    echo.
    pause
    start https://www.python.org/downloads/
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYTHON_VERSION=%%v
echo [√] 检测到Python %PYTHON_VERSION%
echo.

echo [2/4] 检查/安装Python依赖包...
python -c "import yt_dlp" 2>nul
if errorlevel 1 (
    echo    正在安装 yt-dlp...
    pip install yt-dlp --quiet
)

python -c "import faster_whisper" 2>nul
if errorlevel 1 (
    echo    正在安装 faster-whisper...
    pip install faster-whisper --quiet
)

python -c "import requests" 2>nul
if errorlevel 1 (
    echo    正在安装 requests...
    pip install requests --quiet
)

python -c "import imageio_ffmpeg" 2>nul
if errorlevel 1 (
    echo    正在安装 imageio-ffmpeg...
    pip install imageio-ffmpeg --quiet
)

echo [√] Python依赖包检查完成
echo.

echo [3/4] 检查FFmpeg...
python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" >nul 2>&1
if errorlevel 1 (
    echo    正在下载FFmpeg...
    pip install imageio-ffmpeg --quiet
)

python -c "import imageio_ffmpeg; exit(0)" 2>nul
if not errorlevel 1 (
    echo [√] FFmpeg 已就绪
) else (
    echo [警告] FFmpeg 可能存在问题，将尝试继续运行...
)
echo.

echo [4/4] 启动程序...
echo ========================================
echo.
echo    环境检查完成！正在启动程序...
echo.
echo    如需配置API Key，请在界面中输入
echo    首次使用建议点击"保存配置"
echo.
echo ========================================
echo.

python main.py --gui

if errorlevel 1 (
    echo.
    echo [错误] 程序启动失败
    pause
)
