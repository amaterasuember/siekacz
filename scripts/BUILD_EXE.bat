@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

call "%~dp0SETUP_ENV.bat"
if errorlevel 1 exit /b %errorlevel%

echo Instaluje narzedzia budowania...
"%VENV_PY%" -m pip install --disable-pip-version-check pyinstaller pyarmor
if errorlevel 1 goto :failed

echo Generuje manifest integralnosci...
"%VENV_PY%" -m app.integrity
if errorlevel 1 goto :failed

echo Probuje obfuskowac kod zrodlowy...
if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
"%VENV_PY%" -m pyarmor gen --recursive --output .pyarmor_build main.py
if errorlevel 1 (
    echo [WARN] Obfuskacja niedostepna. Buduje EXE bez obfuskacji.
    if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
    goto :build_plain
)

set "PYARMOR_RT="
for /d %%D in (".pyarmor_build\pyarmor_runtime_*") do set "PYARMOR_RT=%%~nxD"
if "!PYARMOR_RT!"=="" (
    echo [WARN] Brak runtime PyArmor. Buduje EXE bez obfuskacji.
    if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
    goto :build_plain
)

echo Buduje wersje przenosna z obfuskacja...
"%VENV_PY%" -m PyInstaller ^
    --noconfirm ^
    --windowed ^
    --name SIEKACZ9000 ^
    --icon "assets\app_icon.ico" ^
    --add-data "assets;assets" ^
    --add-data "database\schema.sql;database" ^
    --add-data "sample_data;sample_data" ^
    --add-data "LICENSE;." ^
    --collect-all ezdxf ^
    --hidden-import app.technical_editor ^
    --hidden-import app.cad_viewer ^
    --hidden-import cad.inspection ^
    --hidden-import import_export.dxf_io ^
    --add-data ".pyarmor_build\!PYARMOR_RT!;!PYARMOR_RT!" ^
    --paths ".pyarmor_build" ^
    --distpath "dist\portable" ^
    --workpath "build" ^
    ".pyarmor_build\main.py"
if errorlevel 1 goto :failed_clean_obf

if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"
goto :cleanup

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
    --add-data "LICENSE;." ^
    --collect-all ezdxf ^
    --hidden-import app.technical_editor ^
    --hidden-import app.cad_viewer ^
    --hidden-import cad.inspection ^
    --hidden-import import_export.dxf_io ^
    --distpath "dist\portable" ^
    --workpath "build" ^
    main.py
if errorlevel 1 goto :failed

:cleanup
if exist "build" rmdir /s /q "build"
if exist "SIEKACZ9000.spec" del /q "SIEKACZ9000.spec"
for %%D in (app ui algorithms core workers import_export database tests __pycache__) do (
    if exist "%%D\__pycache__" rmdir /s /q "%%D\__pycache__"
)
if exist "__pycache__" rmdir /s /q "__pycache__"

echo.
echo Gotowe. Wersja przenosna:
echo %ROOT_DIR%\dist\portable\SIEKACZ9000\SIEKACZ9000.exe
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 0

:failed_clean_obf
if exist ".pyarmor_build" rmdir /s /q ".pyarmor_build"

:failed
echo.
echo Budowanie aplikacji nie powiodlo sie.
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 1
