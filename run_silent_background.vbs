' VBScript to launch Academic & Quiz Auto-Bot completely silently in the background
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

strDir = FSO.GetParentFolderName(WScript.ScriptFullName)
strCmd = "python -m src.main --daemon --interval 600"

WshShell.CurrentDirectory = strDir
' Run command with window style 0 (completely hidden) and bWaitOnReturn = False
WshShell.Run strCmd, 0, False

Set WshShell = Nothing
Set FSO = Nothing
