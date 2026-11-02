import os

startup_dir = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft",
    "Windows",
    "Start Menu",
    "Programs",
    "Startup",
)
target_vbs = os.path.join(r"C:\Users\galaxy\OneDrive\Desktop\meu", "run_silent_background.vbs")
startup_file = os.path.join(startup_dir, "AcademicAutoBot.vbs")

vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{os.path.dirname(target_vbs)}"
WshShell.Run "python -m src.main --daemon --interval 600", 0, False
'''

with open(startup_file, "w", encoding="utf-8") as f:
    f.write(vbs_content)

print(f"Startup entry created successfully at: {startup_file}")
