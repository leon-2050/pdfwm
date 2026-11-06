@echo off
rem PDF 水印工具 - 双击启动（首次会自建 venv 并装依赖）
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 首次运行，正在准备环境...
  uv venv .venv --python 3.11 >nul 2>&1
  uv pip install --python ".venv\Scripts\python.exe" pypdf reportlab pypdfium2 pillow
)
start "" ".venv\Scripts\pythonw.exe" "pdfwm_gui.py" %*
endlocal