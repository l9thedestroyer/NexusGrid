@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Nexus Grid
set "APP=%~dp0game_launcher.py"
set "PYVER=3.13.16"

call :findpython
if defined PYW goto :havepython

echo.
echo  Python 3.10 or newer was not found on this PC.
echo  Nexus Grid runs on Python, so it needs one.
echo.
echo  I can download Python %PYVER% from python.org and install it for you:
echo    - installs for your user account only, no administrator needed
echo    - about 30 MB, and it adds Python to your PATH
echo.
choice /c YN /m "Download and install Python %PYVER% now"
if errorlevel 2 goto :declined

call :installpython
call :findpython
if defined PYW goto :havepython

echo.
echo  Python did not install correctly.
goto :manual

:declined
echo.
echo  No problem.

:manual
echo.
echo  To install it yourself:
echo    1. Open https://www.python.org/downloads/release/python-31316/
echo    2. Download "Windows installer (64-bit)" and run it
echo    3. Tick "Add python.exe to PATH" on the first screen
echo    4. Run this file again
echo.
pause
exit /b 1

:havepython
call :webview2
start "" "%PYW%" "%APP%"
exit /b 0


:findpython
set "PYW="
where py >nul 2>nul
if errorlevel 1 goto :scan
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :scan
for /f "usebackq delims=" %%i in (`py -3 -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"`) do set "PYW=%%i"
if defined PYW if exist "%PYW%" exit /b 0
set "PYW="
:scan
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do call :checkdir "%%~d"
exit /b 0

:checkdir
if defined PYW exit /b 0
if not exist "%~1\python.exe" exit /b 0
if not exist "%~1\pythonw.exe" exit /b 0
"%~1\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PYW=%~1\pythonw.exe"
exit /b 0


:installpython
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
set "PYEXE=%TEMP%\python-%PYVER%-amd64.exe"
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 (
    echo.
    echo  This is a 32-bit version of Windows. Nexus Grid needs 64-bit Windows.
    exit /b 1
)
if exist "%PYEXE%" del "%PYEXE%" >nul 2>nul
echo.
echo  Downloading Python %PYVER% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%PYURL%' -OutFile '%PYEXE%'" >nul 2>nul
if not exist "%PYEXE%" curl.exe -L -s -o "%PYEXE%" "%PYURL%"
if not exist "%PYEXE%" (
    echo  The download failed. Check your internet connection and try again.
    exit /b 1
)
echo  Checking the file is signed by the Python Software Foundation ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-AuthenticodeSignature '%PYEXE%'; if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -like '*Python Software Foundation*') { exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo  The downloaded file failed the signature check, so it was not run.
    del "%PYEXE%" >nul 2>nul
    exit /b 1
)
echo  Installing Python %PYVER% - this takes a minute ...
start /wait "" "%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0 Shortcuts=0
del "%PYEXE%" >nul 2>nul
exit /b 0


:webview2
if exist "%~dp0.webview2_ok" exit /b 0
for %%k in ("HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}") do reg query %%k /v pv >nul 2>nul && goto :wvdone
echo.
echo  Installing the Microsoft Edge WebView2 Runtime, which YouTube needs ...
set "WVEXE=%TEMP%\MicrosoftEdgeWebview2Setup.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile '%WVEXE%'" >nul 2>nul
if exist "%WVEXE%" start /wait "" "%WVEXE%" /silent /install
del "%WVEXE%" >nul 2>nul
:wvdone
echo ok>"%~dp0.webview2_ok"
exit /b 0
