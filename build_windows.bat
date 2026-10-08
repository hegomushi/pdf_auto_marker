@echo off
rem ------------------------------------------------------------------
rem  Build the Windows distribution folder (run on Windows).
rem  Output: dist\PdfAutoMarker\  and  dist\PdfAutoMarker-win64.zip
rem  Requires: Python 3.10+ (python.org installer, with tcl/tk)
rem ------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist .venv-build (
  python -m venv .venv-build || goto :error
)
call .venv-build\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements.txt pyinstaller || goto :error

pyinstaller --noconfirm --clean packaging\pdf_auto_marker.spec || goto :error
python packaging\finalize_dist.py || goto :error

echo.
echo Build completed: dist\PdfAutoMarker
echo Zip file:        dist\PdfAutoMarker-win64.zip
pause
exit /b 0

:error
echo.
echo [ERROR] Build failed.
pause
exit /b 1
