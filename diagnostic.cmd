@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%~dp0tools\bootstrap.py" %*
goto finished
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python "%~dp0tools\bootstrap.py" %*
:finished
set "diagnostic_exit=%errorlevel%"
echo.
if not "%diagnostic_exit%"=="0" echo Diagnostic incomplet ou en erreur. Voir le message ci-dessus.
if not defined CI pause
exit /b %diagnostic_exit%
:missing_python
echo Installez Python 3.10 ou plus depuis python.org, puis relancez diagnostic.cmd.
if not defined CI pause
exit /b 1
