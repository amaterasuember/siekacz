@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

call "%~dp0SETUP_ENV.bat"
if errorlevel 1 exit /b %errorlevel%

rem ── 1. Narzedzia budowania ───────────────────────────────────────────────────
echo Instaluje PyInstaller i PyArmor...
"%VENV_PY%" -m pip install --disable-pip-version-check pyinstaller pyarmor
if errorlevel 1 goto :failed

rem ── 2. Manifest integralnosci ────────────────────────────────────────────────
echo Generuje manifest integralnosci...
"%VENV_PY%" -m app.integrity
if errorlevel 1 goto :failed

rem ── 3. Obfuskacja PyArmor ────────────────────────────────────────────────────
rem PyArmor szyfruje bajtkod tak, ze pliki .pyc w EXE sa nieczytelne.
rem
rem LICENCJA: darmowy tier = max 30 plikow .py. Ten projekt ma ok. 50 plikow,
rem wiec wymagana jest licencja PyArmor Pro ok. 49 USD lifetime:
rem   https://pyarmor.readthedocs.io/en/latest/licenses.html
rem Po zakupie aktywuj: pyarmor reg TWOJ_PLIK_LICENCJI.zip
rem Jezeli nie masz licencji, skrypt automatycznie zbuduje EXE bez obfuskacji.

echo Obfuskuje kod zrodlowy (PyArmor)...
if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"

"%VENV_PY%" -m pyarmor gen --recursive --output .pyarmor_build main.py
if errorlevel 1 (
    echo.
    echo [WARN] Obfuskacja nie powiodla sie. Mozliwe przyczyny:
    echo [WARN]  - Darmowy tier PyArmor: limit 30 plikow, ten projekt ma wiecej.
    echo [WARN]  - Brak licencji. Aktywuj: pyarmor reg PLIK_LICENCJI.zip
    echo [WARN]  - Info: https://pyarmor.readthedocs.io/en/latest/licenses.html
    echo [WARN] Buduje EXE bez obfuskacji...
    echo.
    if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
    goto :build_plain
)

rem Znajdz nazwe katalogu runtime (zmienia sie per wersja PyArmor)
set "PYARMOR_RT="
for /d %%D in (".pyarmor_build\pyarmor_runtime_*") do set "PYARMOR_RT=%%~nxD"
if "!PYARMOR_RT!"=="" (
    echo [WARN] Nie znaleziono katalogu pyarmor_runtime - buduje bez obfuskacji.
    if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
    goto :build_plain
)
echo Znaleziono PyArmor runtime: !PYARMOR_RT!

rem ── 4a. PyInstaller z obfuskowanym kodem ─────────────────────────────────────
echo Buduje wersje przenosna z obfuskacja PyArmor...
"%VENV_PY%" -m PyInstaller ^
    --noconfirm ^
    --windowed ^
    --name SIEKACZ9000 ^
    --icon "assets\app_icon.ico" ^
    --add-data "assets;assets" ^
    --add-data "database\schema.sql;database" ^
    --add-data "sample_data;sample_data" ^
    --add-data ".pyarmor_build\!PYARMOR_RT!;!PYARMOR_RT!" ^
    --paths ".pyarmor_build" ^
    --distpath "dist\portable" ^
    --workpath "build" ^
    ".pyarmor_build\main.py"
if errorlevel 1 goto :failed_clean_obf

if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
goto :cleanup

rem ── 4b. PyInstaller bez obfuskacji (fallback) ────────────────────────────────
:build_plain
echo Buduje wersje przenosna bez obfuskacji...
"%VENV_PY%" -m PyInstaller ^
    --noconfirm ^
    --windowed ^
    --name SIEKACZ9000 ^
    --icon "assets\app_icon.ico" ^
    --add-data "assets;assets" ^
    --add-data "database\schema.sql;database" ^
    --add-data "sample_data;sample_data" ^
    --distpath "dist\portable" ^
    --workpath "build" ^
    main.py
if errorlevel 1 goto :failed

rem ── 5. Sprzatanie ────────────────────────────────────────────────────────────
:cleanup
if exist "build" rmdir /s /q "build"
if exist "SIEKACZ9000.spec" del /q "SIEKACZ9000.spec"
for %%D in (app ui algorithms core workers import_export database tests __pycache__) do (
    if exist "%%D\__pycache__" rmdir /s /q "%%D\__pycache__"
)
if exist "__pycache__" rmdir /s /q "__pycache__"

echo.
echo Gotowe. Przenies caly folder dist\portable\SIEKACZ9000 na inny komputer i uruchom SIEKACZ9000.exe.
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 0

:failed_clean_obf
if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
:failed
echo.
echo Budowanie aplikacji nie powiodlo sie.
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 1
