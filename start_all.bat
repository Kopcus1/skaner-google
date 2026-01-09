@echo off
title System Wesola - LAUNCHER

:: Przejdź do folderu, w którym znajduje się ten skrypt
cd /d "%~dp0"

echo ==========================================
echo    START SYSTEMU "WESOLA" (Google Edition)
echo ==========================================

:: Sprawdzenie czy istnieje venv
if not exist ".venv\Scripts\python.exe" (
    echo [BLAD] Nie znaleziono srodowiska wirtualnego .venv!
    echo Upewnij sie, ze skrypt jest w folderze projektu.
    pause
    exit
)

:: 1. Uruchom Skaner (login.py)
echo [1/4] Startuje Skaner (Kamera)...
start "1. SKANER (login.py)" ".venv\Scripts\python.exe" login.py

:: Czekamy 2 sekundy, żeby kamera zdążyła się zainicjować
timeout /t 2 /nobreak >nul

:: 2. Uruchom Procesor (rotation.py)
echo [2/4] Startuje Procesor (rotation.py)...
start "2. PROCESOR (rotation.py)" ".venv\Scripts\python.exe" rotation.py

:: 3. Uruchom Mapper (wrapper.py)
:: UWAGA: Na screenie plik nazywa się wrapper.py, w kodzie wcześniej był window_mapper.py.
:: Używam nazwy ze screena.
echo [3/4] Startuje Mapper (wrapper.py)...
start "3. MAPPER (wrapper.py)" ".venv\Scripts\python.exe" wrapper.py

:: 4. Uruchom Uploader (uploader.py)
echo [4/4] Startuje Uploader (uploader.py)...
start "4. UPLOADER (uploader.py)" ".venv\Scripts\python.exe" uploader.py

echo ==========================================
echo Wszystkie serwisy zostaly uruchomione w osobnych oknach.
echo Nie zamykaj ich! (Minimalizuj jesli trzeba)
echo ==========================================
pause