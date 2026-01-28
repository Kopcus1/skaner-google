@echo off
title System Wesola - LAUNCHER (FULL SUITE)

:: Przejdz do folderu skryptu
cd /d "%~dp0"

echo ========================================================
echo   START SYSTEMU "WESOLA" (Electron + Python Scripts)
echo ========================================================

:: --- KROK 0: CZYSZCZENIE (Odblokowanie portow i kamery) ---
echo [0/5] Zamykanie starych procesow...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM electron.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1

:: Sprawdzenie srodowiska
if not exist ".venv\Scripts\python.exe" (
    echo [BLAD] Nie znaleziono .venv!
    echo Upewnij sie, ze jestes w dobrym folderze.
    pause
    exit
)

:: --- KROK 1: SERWER GLOWNY (Kamera) ---
echo [1/4] Startuje Skaner (login.py)...
start "1. LOGIN (Kamera)" ".venv\Scripts\python.exe" login.py

:: Czekamy 3 sekundy, zeby Flask zdazyl wstac i zajac kamere
timeout /t 3 /nobreak >nul

:: --- KROK 2: SKRYPTY POMOCNICZE ---

echo [2/4] Startuje Mapper (wrapper.py)...
start "2. WRAPPER" ".venv\Scripts\python.exe" wrapper.py

echo [3/4] Startuje Uploader (uploader.py)...
start "3. UPLOADER" ".venv\Scripts\python.exe" uploader.py

:: --- KROK 3: INTERFEJS ---
echo [4/4] Startuje GUI (Electron)...
if exist "gui-electron" (
    cd gui-electron
    start "4. GUI (Electron)" npm start
    cd ..
) else (
    echo [BLAD] Nie znaleziono folderu gui-electron!
    pause
)

echo ========================================================
echo WSZYSTKIE MODULY URUCHOMIONE.
echo ========================================================
echo Aby zamknac system, zamknij okna konsoli.
pause