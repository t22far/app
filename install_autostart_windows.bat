@echo off
rem Makes Leave Tracker start automatically whenever this PC logs in.
set SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Leave Tracker.lnk
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');$s.TargetPath='%~dp0run_windows.bat';$s.WorkingDirectory='%~dp0';$s.Save()"
echo Done. Leave Tracker will start on its own next time this PC logs in.
pause
