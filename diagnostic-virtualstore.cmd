@echo off
setlocal
if not defined LOCALAPPDATA goto missing_path
if not exist "%LOCALAPPDATA%\VirtualStore\Program Files (x86)\WoWCartographe\maps\" goto missing_path
call "%~dp0diagnostic.cmd" --maps "%LOCALAPPDATA%\VirtualStore\Program Files (x86)\WoWCartographe\maps" %*
exit /b %errorlevel%
:missing_path
echo VirtualStore WoWCartographe introuvable pour cet utilisateur Windows.
echo Utilisez diagnostic.cmd avec --maps pour indiquer le chemin reel.
if not defined CI pause
exit /b 1
