@echo off
chcp 65001 > nul
setlocal

set "APPNEV=YouTube Letolto"

cd /d "%~dp0"

echo ==========================================
echo    %APPNEV% - build
echo ==========================================
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

REM --- PyInstaller ellenorzese / telepitese ---
%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [1/3] PyInstaller telepitese...
    %PY% -m pip install --upgrade pip >nul
    %PY% -m pip install pyinstaller
    if errorlevel 1 (
        echo [HIBA] A PyInstaller telepitese nem sikerult.
        pause
        exit /b 1
    )
) else (
    echo [1/3] PyInstaller rendben.
)

REM --- Fut-e meg a program? Ha igen, a dist\*.exe zarolva van, es a
REM     PyInstaller egy nehezen erthato PermissionError-ral all le. ---
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

REM --- A naplo NEM torolheto: gyakran az egyetlen nyoma egy hibanak ---
echo [2/3] Regi build takaritasa (a naplo megmarad)...
if exist "dist\zeneletolto.log" move /y "dist\zeneletolto.log" "%TEMP%\zeneletolto.log.mentes" >nul
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

REM --- Build ---
echo [3/3] Exe keszitese... (ez eltarthat 1-2 percig)
echo.
%PY% -m PyInstaller --clean --noconfirm YouTubeLetolto.spec
if errorlevel 1 (
    echo.
    echo [HIBA] A build nem sikerult. Nezd meg a fenti uzeneteket.
    if exist "%TEMP%\zeneletolto.log.mentes" (
        if not exist "dist" mkdir "dist"
        move /y "%TEMP%\zeneletolto.log.mentes" "dist\zeneletolto.log" >nul
    )
    pause
    exit /b 1
)

if exist "build" rmdir /s /q "build"

REM --- Korabbi naplo visszatetele ---
if exist "%TEMP%\zeneletolto.log.mentes" (
    move /y "%TEMP%\zeneletolto.log.mentes" "dist\zeneletolto.log" >nul
    echo Korabbi naplo megtartva: dist\zeneletolto.log
)

echo.
echo ==========================================
echo    KESZ!  dist\%APPNEV%.exe
echo ==========================================
echo.
echo Az exe onalloan fut, a yt-dlp-t es az ffmpeg-et
echo elso inditaskor automatikusan letolti.
echo.

if exist "dist\%APPNEV%.exe" explorer "dist"

pause
endlocal
