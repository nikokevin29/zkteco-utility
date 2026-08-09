#!/bin/bash
set -euo pipefail
echo "============================================"
echo " ZKTeco Utility — Linux Build"
echo "============================================"

pip install pyzk openpyxl PySide6 certifi pyinstaller --quiet

pyinstaller --noconfirm --clean --onefile --windowed \
  --name "ZKTeco_Utility_Linux" \
  --add-data "app_icon.png:." \
  --add-data "updater.py:." \
  --collect-all openpyxl \
  --collect-all zk \
  --collect-all PySide6 \
  --collect-all certifi \
  --hidden-import openpyxl \
  --hidden-import openpyxl.styles \
  --hidden-import openpyxl.utils \
  --hidden-import openpyxl.worksheet \
  --hidden-import openpyxl.formatting \
  --hidden-import zk \
  --hidden-import updater \
  --hidden-import certifi \
  --exclude-module pandas \
  --exclude-module numpy \
  --exclude-module matplotlib \
  --exclude-module PIL \
  --exclude-module pytest \
  --exclude-module unittest \
  --exclude-module tkinter \
  zkteco_app.py

echo "Done: dist/ZKTeco_Utility_Linux"
echo "Run: chmod +x dist/ZKTeco_Utility_Linux && ./dist/ZKTeco_Utility_Linux"
