@echo off
REM Double-click this the first time - it shows install progress in a
REM console window. After that, main.pyw (double-click it directly) runs
REM the same app with no console window at all.
python main.py
if errorlevel 1 (
    echo.
    echo Something went wrong - see the messages above.
    pause
)
