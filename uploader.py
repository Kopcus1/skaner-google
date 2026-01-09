import os
import time
import shutil
import sys
from google.cloud import storage
from google.oauth2 import service_account

# --- KONFIGURACJA ---
BUCKET_NAME = "stained-glass-bucket"  # Twoja nazwa bucketa
FOLDER_PREFIX = "input"  # Folder w buckecie

# Ścieżki lokalne
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "content")
INPUT_FOLDER = os.path.join(BASE_DIR, "AI_INPUT")
ARCHIVE_FOLDER = os.path.join(BASE_DIR, "ARCHIVE_UPLOADED")
ERROR_FOLDER = os.path.join(BASE_DIR, "ERROR_UPLOAD")
KEY_PATH = "serviceAccountKey.json"


class CloudUploader:
    def __init__(self):
        self.ensure_dirs()
        self.client = self.authenticate()
        self.bucket = self.connect_bucket()

    def ensure_dirs(self):
        for path in [INPUT_FOLDER, ARCHIVE_FOLDER, ERROR_FOLDER]:
            if not os.path.exists(path):
                os.makedirs(path)

    def authenticate(self):
        if not os.path.exists(KEY_PATH):
            print(f"[CRITICAL] Brak klucza: {KEY_PATH}")
            sys.exit(1)
        try:
            credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
            client = storage.Client(credentials=credentials, project=credentials.project_id)
            return client
        except Exception as e:
            print(f"[AUTH ERROR] {e}")
            sys.exit(1)

    def connect_bucket(self):
        try:
            # Zakładamy, że bucket istnieje (nie sprawdzamy .exists() by uniknąć błędu uprawnień admina)
            bucket = self.client.bucket(BUCKET_NAME)
            print(f"[BUCKET] Polaczono z: {BUCKET_NAME}")
            return bucket
        except Exception as e:
            print(f"[BUCKET ERROR] {e}")
            sys.exit(1)

    def parse_filename(self, filename):
        """
        Parsuje skomplikowaną nazwę z pipeline'u.
        Wejście: AI_READY_flat_p3_scan_[UUID]_[NR]_[TIME].jpg
        Wyjście: (uuid, nr_skanu, pattern_id)
        """
        try:
            # Rozbijamy po podkreślnikach
            parts = filename.split('_')

            # Szukamy kluczowych elementów
            # 1. Pattern ID (np. p3)
            pattern_id = "unknown"
            for part in parts:
                if part.startswith('p') and part[1:].isdigit():
                    pattern_id = part[1:]  # Samo '3'
                    break

            # 2. UUID i NR SKANU
            # Wiemy, że login.py generuje: scan_{UUID}_{NR}_{TIME}.jpg
            # Więc szukamy słowa 'scan' i bierzemy kolejne elementy
            if "scan" in parts:
                scan_index = parts.index("scan")
                uuid = parts[scan_index + 1]
                scan_nr = parts[scan_index + 2]

                return uuid, scan_nr, pattern_id

            return None, None, None

        except Exception as e:
            print(f"[PARSE ERROR] Nie udalo sie sparsowac {filename}: {e}")
            return None, None, None

    def upload_file(self, filename):
        local_path = os.path.join(INPUT_FOLDER, filename)

        # 1. Parsowanie nazwy
        uuid, scan_nr, pattern_id = self.parse_filename(filename)

        if uuid and scan_nr:
            # Nowa, czysta nazwa: input/UUID_NR.jpg
            target_name = f"{FOLDER_PREFIX}/{uuid}_{scan_nr}.jpg"
            print(f"--> Upload: {filename}")
            print(f"    Jako:   {target_name} (Pattern: {pattern_id}) ...", end=" ")
        else:
            # Fallback: Jeśli nazwa jest dziwna, wrzuć starą nazwę
            print(f"[WARN] Nierozpoznany format nazwy. Uzywam oryginalnej.")
            target_name = f"{FOLDER_PREFIX}/{filename}"
            pattern_id = "unknown"

        try:
            blob = self.bucket.blob(target_name)

            # 2. Ustawienie Metadanych (Ważne dla AI!)
            # Dzięki temu nazwa pliku jest czysta, a AI i tak wie, jaki to styl.
            blob.metadata = {
                "pattern_id": pattern_id,
                "original_filename": filename,
                "scan_number": scan_nr,
                "user_uuid": uuid
            }

            # 3. Upload
            blob.upload_from_filename(local_path)
            print("OK.")
            return True

        except Exception as e:
            print(f"\n[UPLOAD FAIL] {e}")
            return False

    def run_loop(self):
        print("--- CLOUD UPLOADER (RENAMER) START ---")
        print(f"Watch: {INPUT_FOLDER}")
        print(f"Target: gs://{BUCKET_NAME}/{FOLDER_PREFIX}/[UUID]_[NR].jpg")

        while True:
            try:
                files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
                files.sort(key=lambda x: os.path.getmtime(os.path.join(INPUT_FOLDER, x)))

                if not files:
                    time.sleep(1)
                    continue

                for filename in files:
                    src_path = os.path.join(INPUT_FOLDER, filename)
                    time.sleep(0.5)

                    if self.upload_file(filename):
                        shutil.move(src_path, os.path.join(ARCHIVE_FOLDER, filename))
                    else:
                        shutil.move(src_path, os.path.join(ERROR_FOLDER, filename))

            except KeyboardInterrupt:
                print("\nZatrzymano Uploader.")
                break
            except Exception as e:
                print(f"[LOOP ERROR] {e}")
                time.sleep(2)


if __name__ == "__main__":
    app = CloudUploader()
    app.run_loop()