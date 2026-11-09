' Lanza vigilante.ps1 sin ventana. Lo ejecuta la tarea programada «Jarvis vigilante».
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\vigilante.ps1""", 0, True
