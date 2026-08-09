@echo off
title ZKTeco Utility - Windows Build
echo ============================================
echo  ZKTeco eFace10 Utility - Windows Build v5.0.3
echo ============================================
echo.

echo [1/3] Install dependencies...
pip install pyzk openpyxl pyinstaller PySide6 certifi --quiet
if %errorlevel% neq 0 (
    echo ERROR: pip install gagal.
    pause & exit /b 1
)

echo.
echo [2/3] Build exe...
pyinstaller --noconfirm --clean --onefile --windowed ^
  --name "ZKTeco_Utility" ^
  --icon "app_icon.ico" ^
  --add-data "app_icon.ico;." ^
  --add-data "app_icon.png;." ^
  --add-data "updater.py;." ^
  --collect-all openpyxl ^
  --collect-all zk ^
  --collect-all PySide6 ^
  --collect-all certifi ^
  --hidden-import openpyxl ^
  --hidden-import openpyxl.styles ^
  --hidden-import openpyxl.styles.fills ^
  --hidden-import openpyxl.styles.fonts ^
  --hidden-import openpyxl.styles.borders ^
  --hidden-import openpyxl.styles.alignment ^
  --hidden-import openpyxl.utils ^
  --hidden-import openpyxl.worksheet ^
  --hidden-import openpyxl.formatting ^
  --hidden-import zk ^
  --hidden-import zk.base ^
  --hidden-import zk.exception ^
  --hidden-import zk.user ^
  --hidden-import zk.attendance ^
  --hidden-import updater ^
  --hidden-import certifi ^
  --exclude-module pandas ^
  --exclude-module tkinter ^
  --exclude-module PIL ^
  --exclude-module numpy ^
  --exclude-module scipy ^
  --exclude-module matplotlib ^
  --exclude-module sklearn ^
  --exclude-module IPython ^
  --exclude-module pytest ^
  --exclude-module unittest ^
  --exclude-module turtle ^
  zkteco_app.py

if %errorlevel% neq 0 (
    echo ERROR: Build gagal.
    pause & exit /b 1
)

echo.
echo [3/3] Done!
echo.
echo EXE: dist\ZKTeco_Utility.exe
echo Taruh EXE + config.json di folder tersendiri.
pause
