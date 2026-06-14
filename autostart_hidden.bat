@echo off
REM Media Dashboard Auto-Start (runs hidden via vbs launcher)
cd /d "C:\Users\27954\Desktop\rmt data\media-dashboard"
start /min "MediaDashboard" "C:\Users\27954\AppData\Local\Python\pythoncore-3.14-64\python.exe" app.py
