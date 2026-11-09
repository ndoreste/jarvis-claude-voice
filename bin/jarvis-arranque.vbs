' Arranca Jarvis en segundo plano (sin ventana) al iniciar sesión.
' Interfaz: http://localhost:5173  ·  API: http://localhost:8000
' Oyente manos libres «Hey Jarvis»: escucha.py (registro en ..\data\escucha.log)
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & dir & "\jarvis.cmd"" gui --no-browser", 0, False
sh.Run """" & dir & "\..\src\.venv\Scripts\pythonw.exe"" """ & dir & "\escucha.py""", 0, False
