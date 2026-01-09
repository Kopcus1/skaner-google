import os
import time
import logging
import functions_framework
import google.generativeai as genai
from google.cloud import storage
from google.cloud import firestore

# --- KONFIGURACJA ---
PROJECT_ID = os.environ.get("GCP_PROJECT")
COLLECTION_NAME = "qr_codes"
# UWAGA: Upewnij się, że ten model jest dostępny/poprawny.
MODEL_NAME = "gemini-3-pro-image-preview"

# --- JEDEN STAŁY PROMPT ---
# Tutaj wpisz instrukcję, która ma być stosowana do KAŻDEGO zdjęcia
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

# --- INICJALIZACJA ---
try:
    # Klienty Google Cloud
    storage_client = storage.Client()
    db = firestore.Client(project=PROJECT_ID)

    # Konfiguracja Gemini API Key
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logging.critical("Brak zmiennej GOOGLE_API_KEY!")
    else:
        genai.configure(api_key=api_key)

    # Inicjalizacja modelu
    model = genai.GenerativeModel(MODEL_NAME)
    logging.info(f"Model {MODEL_NAME} zainicjalizowany.")

except Exception as e:
    logging.critical(f"Init Error: {e}")
    model = None


# --- LOGIKA RETRY ---
def generate_with_api_retry(prompt_parts):
    delays = [0, 4, 8, 16]

    for attempt, delay in enumerate(delays):
        try:
            if delay > 0:
                logging.info(f"AI API: (Próba {attempt + 1}/4) Czekam {delay}s...")
                time.sleep(delay)

            return model.generate_content(prompt_parts)

        except Exception as e:
            logging.warning(f"Błąd API ({e}) w próbie {attempt + 1}.")
            is_resource_error = "429" in str(e) or "Resource exhausted" in str(e) or "503" in str(e)

            if attempt == len(delays) - 1:
                raise e

            if is_resource_error:
                continue
            else:
                raise e

    raise Exception("Nieoczekiwany błąd pętli retry.")


# --- GŁÓWNA FUNKCJA (Trigger Storage) ---
@functions_framework.cloud_event
def process_storage_image(cloud_event):
    """Funkcja uruchamiana automatycznie po wrzuceniu pliku do Bucketa."""
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    # 1. FILTRACJA: Tylko folder input/
    if not file_name.startswith("input/") or not file_name.endswith(('.jpg', '.png', '.jpeg')):
        return  # Ignorujemy inne pliki

    logging.info(f"--- NOWY PLIK: {file_name} ---")

    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)

        # 2. POBIERANIE METADANYCH
        blob.reload()
        metadata = blob.metadata or {}

        # pattern_id już nie wpływa na prompt, ale może być w metadanych
        user_uuid = metadata.get("user_uuid", "unknown")
        scan_nr = metadata.get("scan_number", "1")

        logging.info(f"Metadane: User={user_uuid}, Scan={scan_nr}")

        # 3. POBRANIE OBRAZU DO PAMIĘCI
        image_bytes = blob.download_as_bytes()

        # 4. PRZYGOTOWANIE WSADU DLA AI
        # Zawsze używamy tego samego promptu
        text_prompt = FIXED_PROMPT

        # Struktura dla google.generativeai (Prompt + Obraz)
        image_part = {'mime_type': 'image/jpeg', 'data': image_bytes}
        prompt_parts = [text_prompt, image_part]

        # 5. GENEROWANIE
        logging.info(f"Wysyłam do Gemini stały prompt: {text_prompt}")
        response = generate_with_api_retry(prompt_parts)

        # Walidacja odpowiedzi
        if not response.parts:
            raise Exception("AI zwróciło pustą odpowiedź (blocked?)")

        try:
            result_bytes = response.parts[0].inline_data.data
        except AttributeError:
            raise Exception(f"AI nie zwróciło obrazu. Tekst: {response.text}")

        # 6. ZAPIS WYNIKU DO OUTPUT/
        output_filename = f"output/{user_uuid}_{scan_nr}.png"
        output_blob = bucket.blob(output_filename)

        output_blob.upload_from_string(result_bytes, content_type="image/png")
        logging.info(f"Zapisano wynik: {output_filename}")

        # 7. AKTUALIZACJA FIRESTORE
        if user_uuid != "unknown":
            doc_ref = db.collection(COLLECTION_NAME).document(user_uuid)

            # Aktualizacja wewnątrz StainedGlass
            doc_ref.update({
                "StainedGlass.LastProcessedScan": scan_nr,
                "StainedGlass.status": "ready",
                "StainedGlass.LastUpdate": firestore.SERVER_TIMESTAMP
            })
            logging.info("Zaktualizowano Firestore (StainedGlass).")

    except Exception as e:
        logging.error(f"CRITICAL FAILURE: {e}")
        if 'user_uuid' in locals() and user_uuid != "unknown":
            try:
                db.collection(COLLECTION_NAME).document(user_uuid).update({
                    "StainedGlass.status": "error"
                })
            except Exception as db_err:
                logging.error(f"Nie udało się zapisać błędu w DB: {db_err}")