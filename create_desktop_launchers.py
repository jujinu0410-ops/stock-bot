import os
import winreg
import pathlib

def get_desktop_path() -> pathlib.Path:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        raw_path, _ = winreg.QueryValueEx(key, "Desktop")
        return pathlib.Path(os.path.expandvars(raw_path))
    except Exception:
        return pathlib.Path(os.path.expanduser("~/Desktop"))

desktop = get_desktop_path()
proj_dir = pathlib.Path(r"C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system")

# 1. VBScript Launcher (no CMD window flashing)
vbs_content = '''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\\Users\\jooji\\.gemini\\antigravity\\scratch\\stock_analysis_system"
WshShell.Run """C:\\Users\\jooji\\AppData\\Local\\Programs\\Python\\Python312\\pythonw.exe"" ""C:\\Users\\jooji\\.gemini\\antigravity\\scratch\\stock_analysis_system\\run_gems_scanner.py""", 1, False
'''

# 2. Batch Launcher (CP949 encoded for Windows CMD)
bat_content = '''@echo off
cd /d "C:\\Users\\jooji\\.gemini\\antigravity\\scratch\\stock_analysis_system"
"C:\\Users\\jooji\\AppData\\Local\\Programs\\Python\\Python312\\python.exe" "run_gems_scanner.py"
pause
'''

# Save files to project directory and desktop
(proj_dir / "Gemini_Gems_Scanner.vbs").write_text(vbs_content, encoding="utf-8")
(desktop / "Gemini_Gems_Scanner.vbs").write_text(vbs_content, encoding="utf-8")

(proj_dir / "Gemini_Gems_Scanner.bat").write_text(bat_content, encoding="cp949")
(desktop / "Gemini_Gems_Scanner.bat").write_text(bat_content, encoding="cp949")

print("Created launchers on Desktop successfully:")
print("1.", desktop / "Gemini_Gems_Scanner.vbs")
print("2.", desktop / "Gemini_Gems_Scanner.bat")
