@echo off
setlocal

set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

set "VENV_PY=%ROOT_DIR%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Brak lokalnego srodowiska .venv. Uruchamiam SETUP_ENV.bat...
    call "%~dp0SETUP_ENV.bat"
    if errorlevel 1 exit /b %errorlevel%
)

echo Uruchamiam test stabilnosci SIEKACZ 9000...
"%VENV_PY%" "%ROOT_DIR%\tests\stability_smoke.py"
if errorlevel 1 goto :failed

echo.
echo Testy zakonczone poprawnie.
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 0

:failed
echo.
echo Testy wykryly problem.
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 1
