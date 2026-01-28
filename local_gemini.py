import os
import time
from google import genai
from google.genai import types
from PIL import Image
import io

# --- KONFIGURACJA ---
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "full_content")

# TWÓJ KLUCZ API
API_KEY = "AIzaSyCEPkERAcr2lHweE_7vJiqQhNUBP4GnYck"

# Model (Dla Imagen 3 używaj gemini-2.5-flash-image lub gemini-3-pro-image-preview)
MODEL_NAME = "gemini-3-pro-image-preview"

# --- PROMPT ---
FIXED_PROMPT = """
ROLA
Multimodalny edytor obrazu dla instalacji projection mapping.

CEL
Zamień szkice w taflach szyb na fotorealistyczne witraże inspirowane Stanisławem Wyspiańskim (polska secesja): dekoracyjne kontury, ornament roślinny, malatura na szkle, podział ołowiem. Dodawaj kreatywne elementy i poprawki kompozycji, aby witraże były pełniejsze i bardziej dopracowane. Projekt ma być czytelny z dystansu 5–10 m (duże formy + ornament średniej skali + strefy oddechu), ale z dominacją dużych plam barwnego szkła.

NIEZMIENNE (ABSOLUTNE)
1) Nie zmieniaj perspektywy, kadru ani geometrii: okna/szprosy/ramy/ściany/parapet/grzejnik identyczne.
2) Edytuj WYŁĄCZNIE wnętrza tafli szyb. Zero spill na drewno i ściany (kolorowe światło na ścianach jest OK).
3) Za szybą nie ma świata, obiektów ani drugiej płaszczyzny okna.
4) Zakaz: godrays/volumetric beams, mgła, dym, kurz/particles, bloom, lens flare, bokeh, filmowe poświaty.
5) Zakaz tekstu, watermarków, podpisów.

POMIESZCZENIE
Wnętrze pozostaje ciemne (low-key), bez przesadnego globalnego kontrastu sceny.

PALETA
Spójna paleta dla obu okien.
Jewel tones (szafiry, szmaragdy, rubiny, ametysty, bursztyny, turkusy), nasycone i szlachetne, bez neonów.

ANTI-WHITE + MINIMUM OPAL
Biały papier ze szkicu zawsze zamieniaj na szkło barwione/tonowane.
Twarda zasada: w każdej tafli maksymalnie 10–15% powierzchni może być jasnym szkłem (opal/perłowe/pastelowe).
Duże pola jasnego szkła są zakazane.
Czysta biel dozwolona wyłącznie jako małe highlighty.

DUŻE PLAMY KOLORU (KLUCZOWE)
Preferuj duże, czytelne pola barwionego szkła zamiast wielu małych, jasnych fragmentów.
Tło i “strefy oddechu” mają być realizowane jako duże, spokojne płaszczyzny kolorowego szkła (tonowane, półprzezroczyste, z delikatną teksturą), a nie jako opal lub biel.
Zmniejsz gęstość podziału ołowiem w jasnych obszarach: mniej segmentów, większe kawałki szkła.
Ornamenty mają być średniej skali i wspierać duże pola koloru, nie rozbijać ich na drobnicę.
SEMANTYCZNE DOBIERANIE KOLORU + “POŻYCZANIE” Z SĄSIEDNICH TAFLI
Jeśli szkic ma dużo bieli lub mało koloru:
1) dobierz dominujący kolor tła pod temat tafli,
2) “pożycz” 2–3 dominujące barwy z sąsiednich tafli i użyj ich do dużych pól tła oraz spokojnych plam szkła.
Kolor ma wynikać z tematu i sąsiadów, a nie z białego papieru.

KOMPOZYCJA I SKALA DETALU
Hierarchia:
1) duże formy,
2) ornament średniej skali,
3) detal drobny tylko akcentowo.
Strefy oddechu obowiązkowe, ale w formie dużych pól kolorowego szkła (nie białych).

WIĘCEJ KREATYWNYCH DODATKÓW (DOZWOLONE)
Dodawaj elementy uzupełniające wewnątrz tafli (ramki, pnącza, medaliony, rośliny, fale, gwiazdy), zgodne z tematem.
Możesz spinać tafle wspólnym motywem dekoracyjnym, respektując szprosy.

PODŚWIETLENIE
Neutralne, mocne podświetlenie od tyłu (ok. 4000–5000K), zróżnicowane (hot-spots + falloff + subtelna nierównomierność), bez obiektów za oknem.

CAUSTICS / PROJEKCJE NA WNĘTRZU
Wyraźne, nasycone projekcje koloru na ścianach i parapecie, zgodne z paletą.
Tylko na powierzchniach (bez promieni w powietrzu).

SZKŁO I OŁÓW
Szkło: mikro-bąbelki, inkluzje, smugi cathedral glass, subtelne falowanie i refrakcja.
Tekstura szkła widoczna, ale delikatna; nie jako jednolity “noise”.
Antique glass subtelny do umiarkowanego.
Ołów: czytelny, rzemieślniczy; segmenty średnie/większe, czyste spoiny.

WYJŚCIE
Zwróć tylko finalny obraz.
Priorytet: geometria i granice szyb > brak świata za szybą > minimum opal + duże plamy koloru > reszta.
"""


def setup_client():
    if not API_KEY or API_KEY == "TU_WKLEJ_SWOJ_KLUCZ_API":
        print("BŁĄD: Nie podałeś klucza API w kodzie!")
        exit(1)

    # Nowa inicjalizacja klienta
    client = genai.Client(api_key=API_KEY)

    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)
        print(f"Utworzono folder bazowy: {BASE_DIR}")

    return client


def generate_and_save(input_path, output_path, client):
    try:
        print(f"   -> Generowanie AI...")
        image = Image.open(input_path)

        # 1. KONFIGURACJA
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            candidate_count=1,
            image_config=types.ImageConfig(
                aspect_ratio="3:4"
            ),
            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
            ]
        )

        # 2. WYWOŁANIE
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[FIXED_PROMPT, image],
            config=config
        )

        image_saved = False

        # 3. OBSŁUGA ODPOWIEDZI (POPRAWIONA)
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    try:
                        img_result = part.as_image()
                        # --- POPRAWKA TUTAJ ---
                        # Usunięto format="PNG", bo output_path ma już .png na końcu
                        img_result.save(output_path)
                        # ----------------------
                        image_saved = True
                        print(f"   [V] Zapisano obraz: {os.path.basename(output_path)}")
                        break
                    except Exception as img_err:
                        print(f"   [!] Błąd zapisu obrazu: {img_err}")

        if not image_saved:
            text_content = ""
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.text: text_content += part.text

            if text_content:
                print(f"   [!] Model zwrócił tekst zamiast obrazu: {text_content[:100]}...")
            else:
                print(f"   [!] AI nie wygenerowało obrazu (pusta odpowiedź inline_data).")
            return False

        # Zapis promptu
        folder_path = os.path.dirname(output_path)
        prompt_file_path = os.path.join(folder_path, "prompt.txt")
        with open(prompt_file_path, "w", encoding="utf-8") as f:
            f.write(FIXED_PROMPT)
        print(f"   [V] Zapisano prompt: prompt.txt")

        return True

    except Exception as e:
        print(f"   [X] Błąd API: {e}")
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            print("   -> Rate Limit! Pauza 20s...")
            time.sleep(20)
        return False


def run_watchdog():
    print(f"--- AI WATCHDOG (NEW SDK v1.0) ---")
    print(f"Obserwuję: {BASE_DIR}")
    print(f"Szukam plików: wrap.jpg")
    print("-" * 30)

    # Pobieramy klienta z nowej funkcji setup
    client = setup_client()

    while True:
        try:
            found_new_job = False

            # Spacer po wszystkich podkatalogach
            for root, dirs, files in os.walk(BASE_DIR):

                if "wrap.jpg" in files and "output.png" not in files:

                    folder_name = os.path.basename(root)
                    print(f"\n[AI] Znaleziono nowe zadanie w folderze: {folder_name}")

                    input_path = os.path.join(root, "wrap.jpg")
                    output_path = os.path.join(root, "output.png")

                    if os.path.getsize(input_path) == 0:
                        continue

                    # Przekazujemy client zamiast model
                    success = generate_and_save(input_path, output_path, client)

                    if success:
                        found_new_job = True
                        time.sleep(4)

            if not found_new_job:
                time.sleep(1)

        except KeyboardInterrupt:
            print("\nZatrzymano.")
            break
        except Exception as e:
            print(f"Błąd głównej pętli: {e}")
            time.sleep(2)


if __name__ == "__main__":
    run_watchdog()