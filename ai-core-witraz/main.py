import os
import time
import logging
import functions_framework
from google import genai
from google.genai import types
from google.cloud import storage
from google.cloud import firestore

# --- KONFIGURACJA ---
PROJECT_ID = os.environ.get("GCP_PROJECT")
COLLECTION_NAME = "qr_codes"
MODEL_NAME = "gemini-3-pro-image-preview"

# --- PROMPT (Bezpieczny, zoptymalizowany pod brak blokad) ---
FIXED_PROMPT = """ROLA
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
    storage_client = storage.Client()
    db = firestore.Client(project=PROJECT_ID)
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logging.critical("Brak GOOGLE_API_KEY!")
        ai_client = None
    else:
        ai_client = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
        logging.info("AI Client gotowy.")
except Exception as e:
    logging.critical(f"Init Error: {e}")
    ai_client = None


@functions_framework.cloud_event
def process_storage_image(cloud_event):
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    logging.info(f"TRIGGER DETECTED: {file_name}")

    lower_name = file_name.lower()
    if not file_name.startswith("input/") or not lower_name.endswith(('.jpg', '.png', '.jpeg')):
        logging.info("SKIP: Plik spoza folderu input/ lub nieprawidłowe rozszerzenie.")
        return

    # Inicjalizacja zmiennych
    response = None
    doc_uuid = None
    scan_nr = 1
    result_bytes = None  # Tu będziemy trzymać wynik

    try:
        # 1. PARSOWANIE
        base_name = os.path.basename(file_name)
        name_without_ext = os.path.splitext(base_name)[0]

        if "_" in name_without_ext:
            parts = name_without_ext.rsplit("_", 1)
            doc_uuid = parts[0]
            scan_nr = int(parts[1]) if parts[1].isdigit() else 1
        else:
            doc_uuid = name_without_ext
            scan_nr = 1

        logging.info(f"UUID: {doc_uuid}, Scan: {scan_nr}")

        # 2. POBRANIE
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        image_bytes = blob.download_as_bytes()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        # 3. KONFIGURACJA
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            candidate_count=1,
            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_CIVIC_INTEGRITY", threshold="BLOCK_NONE"),
            ]
        )

        # 4. GENEROWANIE Z PEŁNĄ LOGIKĄ RETRY (Pętla Wytrwałości)
        max_retries = 5

        for attempt in range(1, max_retries + 1):
            try:
                logging.info(f"--- PRÓBA GENEROWANIA {attempt}/{max_retries} ---")

                # A. Strzał do API
                response = ai_client.models.generate_content(
                    model=MODEL_NAME, contents=[FIXED_PROMPT, image_part], config=config
                )

                # B. Walidacja "na gorąco" (wewnątrz pętli)
                if not response or not response.candidates:
                    raise Exception("Pusta odpowiedź API (brak kandydatów).")

                candidate = response.candidates[0]
                finish_reason = candidate.finish_reason

                # Jeśli finish_reason jest zły, rzucamy błąd, żeby wpadło do except i ponowiło próbę
                if finish_reason not in [1, "STOP", "FINISH_REASON_STOP"]:
                    # Logujemy jako warning, ale próbujemy jeszcze raz
                    logging.warning(f"Próba {attempt}: Blokada {finish_reason}. Safety: {candidate.safety_ratings}")
                    raise Exception(f"Blokada AI: {finish_reason}")

                # C. Wyciąganie bajtów
                temp_bytes = None
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            temp_bytes = part.inline_data.data
                            break

                if not temp_bytes:
                    # Sprawdź czy to tekst
                    err_text = "Brak obrazu."
                    if candidate.content and candidate.content.parts:
                        texts = [p.text for p in candidate.content.parts if p.text]
                        if texts: err_text = f"AI zwróciło tekst: {' '.join(texts)}"
                    raise Exception(err_text)

                # D. Jeśli dotarliśmy tutaj -> SUKCES! Przerywamy pętlę.
                result_bytes = temp_bytes
                logging.info(f"SUKCES w próbie {attempt}. Pobrano {len(result_bytes)} bajtów.")
                break

            except Exception as e:
                logging.warning(f"BŁĄD w próbie {attempt}: {e}")
                # Jeśli to była ostatnia próba, rzucamy błąd dalej, żeby zapisać Error w bazie
                if attempt == max_retries:
                    raise Exception(f"Wyczerpano limit prób. Ostatni błąd: {e}")

                # Jeśli nie ostatnia próba, czekamy chwilę (Backoff)
                sleep_time = attempt * 3  # 3s, 6s, ...
                time.sleep(sleep_time)

        # 5. ZAPIS (Tylko jeśli mamy bytes)
        if result_bytes:
            output_filename = f"output/{name_without_ext}.png"
            bucket.blob(output_filename).upload_from_string(result_bytes, content_type="image/png")
            logging.info(f"Zapisano OUTPUT: {output_filename}")

            if doc_uuid:
                pre_mask_filename = f"pre-mask/{doc_uuid}/{name_without_ext}.png"
                bucket.blob(pre_mask_filename).upload_from_string(result_bytes, content_type="image/png")

            # 6. UPDATE DB - SUKCES
            if doc_uuid:
                db.collection(COLLECTION_NAME).document(doc_uuid).set({
                    "StainedGlass": {
                        "LastProcessedScan": scan_nr,
                        "status": "ready",
                        "LastUpdate": firestore.SERVER_TIMESTAMP
                    }
                }, merge=True)
                logging.info("DB Update OK.")
        else:
            # To teoretycznie nie powinno wystąpić dzięki raise w pętli, ale dla pewności:
            raise Exception("Nie udało się uzyskać obrazu mimo prób.")

    except Exception as e:
        logging.error(f"CRITICAL FAILURE PO WSZYSTKICH PRÓBACH: {e}")
        if doc_uuid:
            try:
                db.collection(COLLECTION_NAME).document(doc_uuid).set({
                    "StainedGlass": {"status": "error", "error_msg": str(e)}
                }, merge=True)
            except:
                pass