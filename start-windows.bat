@echo off
REM OI Forge - start na Windows (dwuklik). Wymaga Pythona 3.10+ i g++ (MSYS2/MinGW-w64 lub WinLibs) w PATH.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 -m oiforge %*
  goto :end
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python -m oiforge %*
  goto :end
)
echo Nie znaleziono Pythona 3. Zainstaluj go ze strony https://www.python.org/downloads/ (zaznacz "Add python.exe to PATH").
pause
:end
