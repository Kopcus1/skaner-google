import os

# --- NAPRAWA 1: Wyciszenie "strasznych" logów gRPC przed importami Google ---
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"

import time
from google.cloud import firestore
from google.cloud import storage

# --- KONFIGURACJA ---
TARGET_BUCKET_NAME = "stained-glass-bucket"
LOCAL_DOWNLOAD_FOLDER = r"C:\Users\WesolaPC_02\Desktop\po-swietach\skaner-google-edition\display_system\download"
COLLECTION_NAME = "qr_codes"
FILE_EXTENSION = ".png"

# Konfiguracja Retry
MAX_RETRIES = 5
RETRY_DELAY = 3  # --- NAPRAWA 2: Zwiększone do 3 sekund, żeby nie spamować chmury

try:
    db = firestore.Client()
    storage_client = storage.Client()
except Exception as e:
    print(f"BŁĄD: Nie można połączyć z Google Cloud. {e}")
    exit(1)

os.makedirs(LOCAL_DOWNLOAD_FOLDER, exist_ok=True)


def download_with_retry(blob, local_path, filename):
    for attempt in range(1, MAX_RETRIES + 1):
        # --- NAPRAWA 3: Usunięto blob.reload() - blob.exists() wystarczy i generuje mniej zapytań
        if blob.exists():
            blob.download_to_filename(local_path)
            print(f"   [V] SUKCES: Pobrano {filename}")
            return True
        else:
            print(f"   [!] Synchronizacja Storage... (Próba {attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)

    print(f"   [X] BŁĄD: Plik {filename} nie pojawił się w Storage mimo sygnału od AI.")
    return False


def check_and_download(doc_id, ready_count):
    bucket = storage_client.bucket(TARGET_BUCKET_NAME)

    # Pobieramy wszystko co gotowe (od 1 do LastProcessedScan włącznie)
    for i in range(1, ready_count + 1):

        filename = f"{doc_id}_{i}{FILE_EXTENSION}"
        local_path = os.path.join(LOCAL_DOWNLOAD_FOLDER, filename)
        cloud_path = f"output/{filename}"

        if os.path.exists(local_path):
            continue

        print(f"[{doc_id}] AI zgłasza gotowość pliku nr {i}. Pobieranie...")

        blob = bucket.blob(cloud_path)
        success = download_with_retry(blob, local_path, filename)

        # --- NAPRAWA 4 (NAJWAŻNIEJSZA): Oddech dla API po pobraniu ---
        # Zapobiega błędowi "too_many_pings" / "GOAWAY"
        if success:
            time.sleep(1.5)


def on_snapshot(col_snapshot, changes, read_time):
    # Opcjonalnie: ignorujemy puste zmiany, żeby nie śmiecić w konsoli
    if not changes:
        return

    for change in changes:
        if change.type.name in ['ADDED', 'MODIFIED']:
            doc = change.document
            data = doc.to_dict()
            doc_id = doc.id

            stained_glass_data = data.get('StainedGlass', {})

            # Logika AI Sync
            raw_ai_count = stained_glass_data.get('LastProcessedScan', 0)

            try:
                ai_ready_count = int(raw_ai_count)
            except (ValueError, TypeError):
                ai_ready_count = 0

            if ai_ready_count > 0:
                check_and_download(doc_id, ai_ready_count)


def main():
    print(f"--- Downloader Wesoła (Mode: AI Sync + Anti-Spam) ---")
    print(f"Nasłuchuje pola: StainedGlass.LastProcessedScan")
    print(f"Bucket: {TARGET_BUCKET_NAME}/output")
    print(f"Folder lokalny: {LOCAL_DOWNLOAD_FOLDER}")
    print("-" * 30)

    doc_ref = db.collection(COLLECTION_NAME)
    query_watch = doc_ref.on_snapshot(on_snapshot)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Zatrzymywanie...")
        query_watch.unsubscribe()


if __name__ == "__main__":
    main()