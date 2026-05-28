@echo off
title German Retail Underwriter v1.3
echo.
echo  ============================================
echo   German Retail Underwriter v1.3
echo   Investment Committee Grade
echo  ============================================
echo.
echo  Starting app... browser will open automatically.
echo  To stop: close this window or press Ctrl+C
echo.
cd /d C:\Users\iritg\retail_underwriter
python -m streamlit run "app 7.py" --server.headless false
pause
