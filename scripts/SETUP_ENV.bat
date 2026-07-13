@echo off
setlocal

set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Tworze lokalne srodowisko Pythona w folderze .venv...
    call :find_python
    if errorlevel 1 goto :python_missing

    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :venv_failed
)

echo Sprawdzam i instaluje wymagane biblioteki...
"%VENV_PY%" -m pip install --disable-pip-version-check -r "%ROOT_DIR%\requirements.txt"
if errorlevel 1 goto :install_failed

exit /b 0

:find_python
if defined SIEKACZ_PYTHON (
    "%SIEKACZ_PYTHON%" -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        set PYTHON_CMD="%SIEKACZ_PYTHON%"
        exit /b 0
    )
)

py -3.14 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.14"
    exit /b 0
)

py -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.13"
    exit /b 0
)

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
    exit /b 0
)

py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.11"
    exit /b 0
)

py -3.10 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.10"
    exit /b 0
)

python -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    exit /b 0
)

exit /b 1

:python_missing
echo.
echo Nie znaleziono Pythona. Zainstaluj Python 3.10 lub nowszy, a potem uruchom ten plik ponownie.
echo Najprosciej pobrac instalator z https://www.python.org/downloads/windows/
pause
exit /b 1

:venv_failed
echo.
echo Nie udalo sie utworzyc srodowiska .venv.
pause
exit /b 1

:install_failed
echo.
echo Nie udalo sie zainstalowac bibliotek z requirements.txt.
echo Sprawdz polaczenie z internetem i sprobuj ponownie.
pause
exit /b 1
