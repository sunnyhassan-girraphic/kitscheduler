@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Couldn't find venv\Scripts\python.exe in this folder.
    echo Make sure safe_migrate.bat is sitting directly inside your
    echo kitscheduler folder, next to manage.py and the venv folder.
    echo.
    pause
    exit /b 1
)

venv\Scripts\python.exe safe_migrate.py
