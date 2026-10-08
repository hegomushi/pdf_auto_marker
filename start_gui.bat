@echo off
rem Launch the PDF auto marker GUI (double-click this file)
cd /d "%~dp0"
python -c "import pymupdf, yaml, tkinter" 2>nul || (
  echo [ERROR] Python or required packages were not found.
  echo Please run:  pip install -r requirements.txt
  pause
  exit /b 1
)
start "" pythonw -m pdf_auto_marker.gui %*
