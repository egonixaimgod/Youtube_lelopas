@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul

set "APPNEV=YouTube Letolto"

cd /d "%~dp0"

echo ==========================================
echo    YouTube Letolto - Auto Rebuild ^& Release
echo ==========================================
echo.

REM --- Fut-e meg a program? Ha igen, a dist\*.exe zarolva van ---
tasklist /fi "imagename eq %APPNEV%.exe" 2>nul | find /i "%APPNEV%.exe" >nul
if %errorlevel%==0 (
    echo [FIGYELEM] A "%APPNEV%.exe" fut, ezert nem irhato felul.
    set /p VALASZ="Bezarjam most? [i/n] "
    if /i "!VALASZ!"=="i" (
        taskkill /f /im "%APPNEV%.exe" >nul 2>&1
        echo Bezarva.
    ) else (
        echo Zard be a programot, es inditsd ujra.
        pause
        exit /b 1
    )
)

echo [1/4] Build szam novelese...
python bump_build.py > temp_build.txt
if errorlevel 1 (
    del temp_build.txt 2>nul
    echo [!] A build szam noveles nem sikerult.
    pause
    exit /b 1
)
set /p NEW_BUILD=<temp_build.txt
del temp_build.txt
echo      Uj build: %NEW_BUILD%

echo.
echo [2/4] Program leforditasa (PyInstaller)...
REM A naplot a build nem torolheti: gyakran az egyetlen nyoma egy hibanak.
if exist "dist\zeneletolto.log" move /y "dist\zeneletolto.log" "%TEMP%\zeneletolto.log.mentes" >nul
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

python -m PyInstaller --clean --noconfirm YouTubeLetolto.spec
if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Hiba a build soran! Megszakitjuk a folyamatot.
    if exist "%TEMP%\zeneletolto.log.mentes" (
        if not exist "dist" mkdir "dist"
        move /y "%TEMP%\zeneletolto.log.mentes" "dist\zeneletolto.log" >nul
    )
    pause
    exit /b %ERRORLEVEL%
)
if exist "build" rmdir /s /q "build"
if exist "%TEMP%\zeneletolto.log.mentes" move /y "%TEMP%\zeneletolto.log.mentes" "dist\zeneletolto.log" >nul

echo.
echo [3/4] Feltoltes a GitHubra (Git Push)...
git add .
git add -f "dist/%APPNEV%.exe"
git commit -m "Release: Build %NEW_BUILD% (Auto-Build)"
git push
if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] A git push nem sikerult - ellenorizd a bejelentkezest ^(gh auth login^).
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [4/4] GitHub Release keszitese...
where gh >nul 2>&1
if %errorlevel%==0 (
    gh release create "v2.0.%NEW_BUILD%" "dist/%APPNEV%.exe" ^
        --title "YouTube Letolto v2.0 build %NEW_BUILD%" ^
        --notes "Automatikus kiadas. Build %NEW_BUILD%." 2>nul
    if errorlevel 1 (
        echo      [i] A release keszites kimaradt ^(mar letezik, vagy nincs jogosultsag^).
    ) else (
        echo      Release kesz: v2.0.%NEW_BUILD%
    )
) else (
    echo      [i] A "gh" parancs nem talalhato, a release keszites kimarad.
)

echo.
echo ==========================================
echo    SIKERES KIADAS: Build %NEW_BUILD%
echo ==========================================
pause
endlocal
