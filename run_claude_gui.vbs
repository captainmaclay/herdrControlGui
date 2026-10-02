Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "wsl.exe -d Ubuntu bash -lc ""/usr/local/bin/claude-gui""", 0, False
