import os

startup_dir = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft",
    "Windows",
    "Start Menu",
    "Programs",
    "Startup",
)
startup_file = os.path.join(startup_dir, "AcademicAutoBot.vbs")

vbs_content = '''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\\Users\\galaxy\\OneDrive\\Desktop\\meu"
WshShell.Run """C:\\Users\\galaxy\\AppData\\Local\\Programs\\Python\\Python314\\python.exe"" ""C:\\Users\\galaxy\\OneDrive\\Desktop\\meu\\watchdog.py""", 0, False
'''

with open(startup_file, "w", encoding="utf-8") as f:
    f.write(vbs_content)

print(f"Updated: {startup_file}")
