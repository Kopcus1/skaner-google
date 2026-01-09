import os
import time
import shutil
import random
from pythonosc import udp_client
from PIL import Image

# --- KONFIGURACJA ---
DIR_LIBRARY = "library"  # Stała baza witraży (JPG)
DIR_DOWNLOAD = "download"  # Nowe pliki (Mogą być PNG)
DIR_PROCESSED = "processed"  # Archiwum
DIR_STAGING = "stage"  # Folder dla TD (slot_0.jpg, slot_1.jpg)

TD_IP = "127.0.0.1"
TD_PORT = 10000
SLIDE_DURATION = 45  # Całkowity czas wyświetlania jednego slajdu
TRANSITION_BUFFER = 2.0  # Czas na przełączenie (User: "Odczekanie sekundy")
# Dałem 2s dla bezpieczeństwa, żeby crossfade w TD na pewno się skończył

# --- INICJALIZACJA ---
for folder in [DIR_LIBRARY, DIR_DOWNLOAD, DIR_PROCESSED, DIR_STAGING]:
    os.makedirs(folder, exist_ok=True)

client = udp_client.SimpleUDPClient(TD_IP, TD_PORT)

# --- ZMIENNE GLOBALNE DO STANU KOLEJKI ---
library_index_global = 0


def get_next_image_path():
    """Decyduje, jaki plik ma być następny (Priorytet vs Biblioteka)"""
    global library_index_global

    # 1. Sprawdź czy są nowe pliki od ludzi (PRIORYTET)
    try:
        downloads = [f for f in os.listdir(DIR_DOWNLOAD) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        if downloads:
            # Sortuj od najstarszego (kolejka FIFO)
            downloads.sort(key=lambda x: os.path.getctime(os.path.join(DIR_DOWNLOAD, x)))
            full_path = os.path.join(DIR_DOWNLOAD, downloads[0])
            print(f"[NEXT] Wybrano plik użytkownika: {downloads[0]}")
            return full_path, True  # True = to jest plik priorytetowy
    except Exception as e:
        print(f"[ERR] Błąd odczytu download: {e}")

    # 2. Jeśli nie, weź z biblioteki (IDLE)
    try:
        library_files = [f for f in os.listdir(DIR_LIBRARY) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        if not library_files:
            return None, False

        # Karuzela
        current_img = library_files[library_index_global % len(library_files)]
        library_index_global += 1

        full_path = os.path.join(DIR_LIBRARY, current_img)
        print(f"[NEXT] Wybrano z biblioteki: {current_img}")
        return full_path, False  # False = to nie jest priorytet
    except Exception as e:
        print(f"[ERR] Błąd odczytu biblioteki: {e}")
        return None, False


def update_slot_content(slot_index):
    """
    Pobiera następny obraz, konwertuje go i wrzuca do wskazanego slotu.
    Zwraca True jeśli udało się wrzucić plik.
    """
    source_path, is_priority = get_next_image_path()

    if not source_path:
        print("[WARN] Brak plików do wyświetlenia!")
        return False

    # Konfiguracja nazw
    filename = f"slot_{slot_index}.jpg"
    temp_filename = f"temp_{filename}"
    target_path = os.path.join(DIR_STAGING, filename)
    temp_path = os.path.join(DIR_STAGING, temp_filename)

    # 1. Konwersja i zapis (PNG/JPG -> JPG)
    try:
        with Image.open(source_path) as img:
            rgb_im = img.convert('RGB')
            rgb_im.save(temp_path, format='JPEG', quality=95)

        with open(temp_path, 'r+') as f:
            os.fsync(f.fileno())

        os.replace(temp_path, target_path)
        print(f"[UPDATE] Zaktualizowano ukryty {filename}")

    except Exception as e:
        print(f"[ERR] Błąd aktualizacji slotu: {e}")
        if os.path.exists(temp_path): os.remove(temp_path)
        return False

    # 2. Archiwizacja (jeśli to był plik z download)
    if is_priority:
        fname = os.path.basename(source_path)
        processed_path = os.path.join(DIR_PROCESSED, fname)
        if os.path.exists(processed_path):
            processed_path = os.path.join(DIR_PROCESSED, f"{int(time.time())}_{fname}")

        try:
            shutil.move(source_path, processed_path)
            print(f"[ARCHIVE] Przeniesiono do processed.")
        except:
            pass

    return True


def main():
    print("--- DISPLAY MANAGER (DOUBLE BUFFERING) ---")

    # KROK 0: INIT - Musimy zapełnić OBA sloty na start,
    # żeby pierwsze przełączenie nie pokazało czarnego ekranu.
    print("[INIT] Przygotowanie Slotu 0...")
    update_slot_content(0)
    print("[INIT] Przygotowanie Slotu 1...")
    update_slot_content(1)

    # Zakładamy, że na starcie TD pokazuje Slot 0 (wartość 0.0 na mix)
    current_visible_slot = 0
    client.send_message("/mix", float(current_visible_slot))
    print("[INIT] Start pętli. Wyświetlam Slot 0.")

    # Czekamy chwilę na start
    time.sleep(SLIDE_DURATION)

    while True:
        # LOGIKA PĘTLI ZGODNA Z TWOIM OPISEM

        # Obliczamy, na który slot chcemy się przełączyć
        # Jeśli teraz widać 0, chcemy przełączyć na 1.
        target_slot = 1 - current_visible_slot

        # 1. PRZEŁĄCZENIE (np. ze slot_0 na slot_1)
        print(f"\n--- PRZEŁĄCZENIE NA SLOT {target_slot} ---")
        client.send_message("/mix", float(target_slot))

        # 2. ODCZEKANIE SEKUNDY (Bufor na przejście crossfade)
        time.sleep(TRANSITION_BUFFER)

        # 3. ZMIANA ZDJĘCIA NA SLOCIE UKRYTYM
        # Skoro przełączyliśmy na target_slot, to ukryty jest ten "stary" (current_visible_slot)
        hidden_slot = current_visible_slot
        print(f"[BACKGROUND] Podmieniam zdjęcie w ukrytym Slot {hidden_slot}...")
        update_slot_content(hidden_slot)

        # 4. MIJA RESZTA CZASU
        # Musimy odjąć czas bufora od głównego czasu
        remaining_time = SLIDE_DURATION - TRANSITION_BUFFER
        if remaining_time < 0: remaining_time = 0

        print(f"[WAIT] Czekam {remaining_time}s...")
        time.sleep(remaining_time)

        # Aktualizacja stanu - teraz widoczny jest ten, na który przełączyliśmy w kroku 1
        current_visible_slot = target_slot


if __name__ == "__main__":
    main()