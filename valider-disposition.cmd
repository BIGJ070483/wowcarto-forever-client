@echo off
setlocal DisableDelayedExpansion
if "%~1"=="" goto usage
if /i not "%~x1"==".json" goto usage
if not exist "%~1" goto usage
"%~dp0diagnostic.cmd" --layout %*
:usage
echo Glissez layout-observed.json sur ce lanceur, apres avoir releve les observations dans validation.html.
echo Les sources par defaut sont celles de la mission. Pour d autres chemins, utilisez diagnostic.cmd --layout avec ses options.
if not defined CI pause
exit /b 1
