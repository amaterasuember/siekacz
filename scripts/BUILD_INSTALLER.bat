@echo off
setlocal

set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

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
"%ISCC%" "%ROOT_DIR%\installer_src\SIEKACZ9000.iss"
if errorlevel 1 goto :failed

if exist "build" rmdir /s /q "build"
if exist "SIEKACZ9000.spec" del /q "SIEKACZ9000.spec"

echo.
echo Gotowe. Instalator znajduje sie tutaj:
echo %ROOT_DIR%\dist\installer\SIEKACZ9000_Setup.exe
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 0

:failed
echo.
echo Budowanie instalatora nie powiodlo sie.
if not "%SIEKACZ_NO_PAUSE%"=="1" pause
exit /b 1
