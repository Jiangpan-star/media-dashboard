Set objShell = CreateObject("WScript.Shell")
objShell.Run "cmd /c cd /d ""C:\Users\27954\Desktop\rmt data\media-dashboard"" && ""C:\Users\27954\AppData\Local\Python\pythoncore-3.14-64\python.exe"" app.py >> app_log.txt 2>&1", 0, False
WScript.Sleep 3000
objShell.Run "http://localhost:8765", 1, False
