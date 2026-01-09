import os
from tkinter import Tk, filedialog
from PIL import Image


def convert_png_to_jpg():
    # 1. Ukrywamy główne okno Tkinter (chcemy tylko popup)
    root = Tk()
    root.withdraw()

    print("Wybierz folder w okienku...")
    folder_path = filedialog.askdirectory(title="Wybierz folder z plikami PNG")

    if not folder_path:
        print("Anulowano wybór folderu.")
        return

    print(f"--- START: {folder_path} ---")

    count = 0

    # 2. Pętla po plikach
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(".png"):
            full_path_png = os.path.join(folder_path, filename)

            # Zmiana rozszerzenia w nazwie
            filename_jpg = os.path.splitext(filename)[0] + ".jpg"
            full_path_jpg = os.path.join(folder_path, filename_jpg)

            try:
                with Image.open(full_path_png) as img:
                    # WAŻNE: PNG ma przezroczystość (RGBA), JPG nie.
                    # Musimy przekonwertować na RGB (tło stanie się białe/czarne zależnie od wersji biblioteki, zazwyczaj czarne)
                    rgb_im = img.convert('RGB')

                    # Zapis jako JPG z dobrą jakością
                    rgb_im.save(full_path_jpg, quality=95)

                print(f"[OK] {filename} -> {filename_jpg}")
                count += 1

                # Opcjonalnie: Usuń stary plik PNG, jeśli chcesz zrobić czystkę
                # os.remove(full_path_png)

            except Exception as e:
                print(f"[BŁĄD] Nie udało się przekonwertować {filename}: {e}")

    print(f"--- KONIEC. Przekonwertowano plików: {count} ---")
    input("Naciśnij Enter, aby zamknąć...")


if __name__ == "__main__":
    convert_png_to_jpg()