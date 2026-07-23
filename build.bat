@echo off
chcp 65001 >nul
setlocal

set "APPNEV=YouTube Letolto"
set "FORRAS=zeneletolto.py"

cd /d "%~dp0"

echo ============================================
echo   %APPNEV% - build
echo ============================================
echo.

REM --- Python ellenorzese ---
where py >nul 2>&1
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [HIBA] Nem talalhato Python. Telepitsd innen: https://www.python.org/downloads/
        echo        Telepiteskor pipald be az "Add python.exe to PATH" opciot!
        pause
        exit /b 1
    )
    set "PY=python"
)

for /f "delims=" %%v in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
echo [1/4] Python %PYVER% rendben.

REM --- PyInstaller ellenorzese / telepitese ---
%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [2/4] PyInstaller nincs telepitve, telepites...
    %PY% -m pip install --upgrade pip >nul
    %PY% -m pip install pyinstaller
    if errorlevel 1 (
        echo [HIBA] A PyInstaller telepitese nem sikerult.
        pause
        exit /b 1
    )
) else (
    echo [2/4] PyInstaller rendben.
)

REM --- Fut-e meg a program? Ha igen, a dist\*.exe zarolva van ---
tasklist /fi "imagename eq %APPNEV%.exe" 2>nul | find /i "%APPNEV%.exe" >nul
if %errorlevel%==0 (
    echo.
    echo [FIGYELEM] A "%APPNEV%.exe" jelenleg fut, ezert nem irhato felul.
    set /p VALASZ="Bezarjam most? [i/n] "
    if /i "%VALASZ%"=="i" (
        taskkill /f /im "%APPNEV%.exe" >nul 2>&1
        echo Bezarva.
    ) else (
        echo Zard be a programot, es inditsd ujra a buildet.
        pause
        exit /b 1
    )
)

REM --- Regi build torlese (a naplot NEM bantjuk: az a hibakereses alapja) ---
echo [3/4] Regi build torlese...
if exist "build" rmdir /s /q "build"
if exist "dist\zeneletolto.log" move /y "dist\zeneletolto.log" "%TEMP%\zeneletolto.log.mentes" >nul
if exist "dist" rmdir /s /q "dist"
if exist "%APPNEV%.spec" del /q "%APPNEV%.spec"

REM --- Build ---
echo [4/4] Exe keszitese... (ez eltarthat 1-2 percig)
echo.
%PY% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --clean ^
    --noconfirm ^
    --name "%APPNEV%" ^
    "%FORRAS%"

if errorlevel 1 (
    echo.
    echo [HIBA] A build nem sikerult. Nezd meg a fenti uzeneteket.
    pause
    exit /b 1
)

REM --- Takaritas: a build mappa es a spec fajl nem kell ---
if exist "build" rmdir /s /q "build"
if exist "%APPNEV%.spec" del /q "%APPNEV%.spec"

REM --- Korabbi naplo visszatetele ---
if exist "%TEMP%\zeneletolto.log.mentes" (
    move /y "%TEMP%\zeneletolto.log.mentes" "dist\zeneletolto.log" >nul
    echo Korabbi naplo megtartva: dist\zeneletolto.log
)

echo.
echo ============================================
echo   KESZ!
echo   dist\%APPNEV%.exe
echo ============================================
echo.
echo Az exe onalloan fut, a yt-dlp-t es az ffmpeg-et
echo elso inditaskor automatikusan letolti.
echo.

REM --- Dist mappa megnyitasa ---
if exist "dist\%APPNEV%.exe" explorer "dist"

pause
endlocal
