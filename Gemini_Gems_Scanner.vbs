Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system"
WshShell.Run """C:\Users\jooji\AppData\Local\Programs\Python\Python312\pythonw.exe"" ""C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\run_gems_scanner.py""", 1, False
