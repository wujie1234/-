@echo off
rem Human Typer launcher - ASCII only on purpose, do not add non-ASCII text here
setlocal enabledelayedexpansion

set "SCRIPT="
for %%F in ("%~dp0*.py") do set "SCRIPT=%%~fF"
if not defined SCRIPT (
    echo [ERROR] Cannot find the main .py file next to this launcher.
    pause
    exit /b 1
)

set "PY="
if exist "%USERPROFILE%\miniconda3\pythonw.exe" set "PY=%USERPROFILE%\miniconda3\pythonw.exe"
if not defined PY if exist "%USERPROFILE%\anaconda3\pythonw.exe" set "PY=%USERPROFILE%\anaconda3\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not defined PY if exist "C:\Python313\pythonw.exe" set "PY=C:\Python313\pythonw.exe"
if not defined PY if exist "C:\Python312\pythonw.exe" set "PY=C:\Python312\pythonw.exe"
if not defined PY if exist "C:\Python311\pythonw.exe" set "PY=C:\Python311\pythonw.exe"

if not defined PY (
    for %%P in (pythonw.exe) do if not defined PY set "PY=%%~$PATH:P"
)

if not defined PY (
    echo [ERROR] Python 3.8+ with tkinter was not found.
    echo Install Python from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

start "" "%PY%" "%SCRIPT%"
