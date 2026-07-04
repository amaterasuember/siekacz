@echo off
setlocal EnableExtensions

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

set "VENV_PY=%ROOT_DIR%.venv\Scripts\python.exe"

echo ============================================================
echo  SIEKACZ 9000 - START AKTUALNEGO KODU ZRODLOWEGO
echo ============================================================
echo  Folder projektu:
echo  %ROOT_DIR%
echo.
echo  Uruchamiany plik:
echo  %ROOT_DIR%main.py
echo.
echo  Uwaga: ten launcher NIE uruchamia starego EXE z dist/portable.
echo  Do testowania zmian uzywaj tylko tego pliku: START_SIEKACZ.bat
echo ============================================================
echo.

if not exist "%VENV_PY%" (
    call "%ROOT_DIR%scripts\SETUP_ENV.bat"
    if errorlevel 1 exit /b %errorlevel%
) else (
    "%VENV_PY%" -c "import PySide6, reportlab, openpyxl" >nul 2>nul
    if errorlevel 1 (
        call "%ROOT_DIR%scripts\SETUP_ENV.bat"
        if errorlevel 1 exit /b %errorlevel%
    )
)

if /i "%~1"=="--check" (
    echo Launcher OK.
    exit /b 0
)

set "PYTHONPATH=%ROOT_DIR%;%PYTHONPATH%"
set "SIEKACZ_SOURCE_LAUNCHER=%ROOT_DIR%START_SIEKACZ.bat"

echo Uruchamiam aplikacje z aktualnych plikow...
echo Jesli masz otwarte stare okno SIEKACZA, zamknij je przed testowaniem.
echo.

"%VENV_PY%" "%ROOT_DIR%main.py"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Aplikacja zakonczyla sie z bledem: %EXIT_CODE%
    echo Log znajdziesz tutaj:
    echo %USERPROFILE%\.cut_optimizer_desktop\siekacz9000.log
    echo.
    pause
)

exit /b %EXIT_CODE%
