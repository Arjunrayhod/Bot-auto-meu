' VBScript to launch Academic & Quiz Auto-Bot completely silently in the background with 24/7 Watchdog
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

strDir = FSO.GetParentFolderName(WScript.ScriptFullName)
strCmd = """C:\Users\galaxy\AppData\Local\Programs\Python\Python314\python.exe"" """ & strDir & "\watchdog.py"""

WshShell.CurrentDirectory = strDir
' Run command with window style 0 (completely hidden) and bWaitOnReturn = False
WshShell.Run strCmd, 0, False

Set WshShell = Nothing
Set FSO = Nothing
