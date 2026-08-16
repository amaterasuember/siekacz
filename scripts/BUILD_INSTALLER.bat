@echo off
setlocal

set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

call "%~dp0SETUP_ENV.bat"
if errorlevel 1 goto :failed
set "VENV_PY=%ROOT_DIR%\.venv\Scripts\python.exe"
set "VERSION_FILE=%TEMP%\SIEKACZ9000_version_%RANDOM%_%RANDOM%.txt"
"%VENV_PY%" -c "import re; from app.version import APP_VERSION; version=APP_VERSION.strip(); parts=re.findall(r'\d+', version); assert parts, 'APP_VERSION must contain a number'; print(version); print('.'.join((parts + ['0'] * 4)[:4]))" > "%VERSION_FILE%"
if errorlevel 1 goto :failed
set /p "APP_VERSION=" < "%VERSION_FILE%"
for /f "usebackq skip=1 delims=" %%V in ("%VERSION_FILE%") do if not defined APP_FILE_VERSION set "APP_FILE_VERSION=%%V"
del /q "%VERSION_FILE%" >nul 2>nul
if not defined APP_VERSION goto :version_failed
if not defined APP_FILE_VERSION goto :version_failed
goto :version_ready

:version_failed
echo Nie udalo sie odczytac wersji z app\version.py.
goto :failed

:version_ready
echo Buduje instalator dla wersji %APP_VERSION% ^(Windows: %APP_FILE_VERSION%^) ...

echo Buduje aplikacje przenosna...
call "%~dp0BUILD_EXE.bat"
if errorlevel 1 goto :failed

set "ISCC="
for %%P in (
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist "%%~P" set "ISCC=%%~P"
)

if not defined ISCC (
    for /f "delims=" %%P in ('where ISCC.exe 2^>nul') do (
        if not defined ISCC set "ISCC=%%P"
    )
)

if not defined ISCC (
    echo.
    echo Nie znaleziono Inno Setup Compiler.
    echo Probuje zainstalowac Inno Setup przez winget...
    winget install --id JRSoftware.InnoSetup -e --silent --accept-source-agreements --accept-package-agreements

    for %%P in (
        "%ProgramFiles%\Inno Setup 6\ISCC.exe"
        "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
        "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
    ) do (
        if exist "%%~P" set "ISCC=%%~P"
    )
)

if not defined ISCC (
    echo.
    echo Nadal nie znaleziono ISCC.exe.
    echo Zainstaluj Inno Setup 6 i uruchom ten plik ponownie:
    echo https://jrsoftware.org/isinfo.php
    goto :failed
)

echo.
echo Buduje instalator SIEKACZ9000_Setup.exe...
if exist "dist\installer" rmdir /s /q "dist\installer"
mkdir "dist\installer"
"%ISCC%" "/DMyAppVersion=%APP_VERSION%" "/DMyAppFileVersion=%APP_FILE_VERSION%" "%ROOT_DIR%\installer_src\SIEKACZ9000.iss"
if errorlevel 1 goto :failed

powershell -NoProfile -Command "$installer=Get-Item 'dist\installer\SIEKACZ9000_Setup.exe'; $fileVersion=$installer.VersionInfo.FileVersion.Trim(); $productVersion=$installer.VersionInfo.ProductVersion.Trim(); if ($fileVersion -ne '%APP_FILE_VERSION%') { Write-Error ('Installer file version mismatch: expected %APP_FILE_VERSION%, got ' + $fileVersion); exit 1 }; if ($productVersion -ne '%APP_FILE_VERSION%') { Write-Error ('Installer product version mismatch: expected %APP_FILE_VERSION%, got ' + $productVersion); exit 1 }; Write-Host ('Verified release %APP_VERSION% (Windows: ' + $fileVersion + ')')"
if errorlevel 1 goto :failed
copy /y "dist\installer\SIEKACZ9000_Setup.exe" "dist\installer\SIEKACZ9000_Setup_v%APP_VERSION%.exe" >nul
if errorlevel 1 goto :failed
copy /y "dist\installer\SIEKACZ9000_Setup.exe" "dist\installer\SIEKACZ9000_Setup_v%APP_VERSION%_windows-x64.exe" >nul
if errorlevel 1 goto :failed

if exist "build" rmdir /s /q "build"
if exist "SIEKACZ9000.spec" del /q "SIEKACZ9000.spec"

echo.
echo Gotowe. Instalator znajduje sie tutaj:
echo %ROOT_DIR%\dist\installer\SIEKACZ9000_Setup.exe
echo %ROOT_DIR%\dist\installer\SIEKACZ9000_Setup_v%APP_VERSION%.exe
echo %ROOT_DIR%\dist\installer\SIEKACZ9000_Setup_v%APP_VERSION%_windows-x64.exe
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 0

:failed
echo.
echo Budowanie instalatora nie powiodlo sie.
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 1
