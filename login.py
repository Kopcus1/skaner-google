import cv2
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import time
import sys
import os
import datetime
import math
import threading
import shutil
from flask import Flask, Response
from flask_socketio import SocketIO

# --- KONFIGURACJA DEBUGOWANIA ---
sys.stdout.reconfigure(encoding='utf-8')

print("=========================================")
print("   SYSTEM WESOLA - CLEAN UI + AUTO FOCUS ")
print("=========================================")

# --- SERWER ---
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- KONFIGURACJA ŚCIEŻEK ---
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "content")
OUTPUT_RAW_DIR = os.path.join(BASE_DIR, "RAW_PHOTO")
OUTPUT_CROPPED_DIR = os.path.join(BASE_DIR, "CROPPED")
EXTRA_OUTPUT_ROOT = os.path.join(CURRENT_DIR, "full_content")
FIREBASE_KEY_PATH = "serviceAccountKey.json"
COLLECTION_NAME = "qr_codes"

# --- PARAMETRY ---
CAMERA_INDEX = 2
TRIGGER_TIME = 3
SUCCESS_DURATION = 10
ERROR_DURATION = 10
MIN_QR_COUNT = 4
MAX_SCANS = 5
SESSION_TIMEOUT = 60
MARKER_MEMORY_DURATION = 0.2
MAX_ABORTS_BEFORE_RESET = 5  # Ile razy można zgubić tracking zanim zresetujemy focus

# Parametry obrazu wynikowego (High Res)
FINAL_WIDTH = 486 * 3
FINAL_HEIGHT = 727 * 3


# --- HELPERY GEOMETRYCZNE ---
def distance(p1, p2):
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def estimate_missing_point(markers):
    all_keys = ['TL', 'TR', 'BR', 'BL']
    missing = [k for k in all_keys if k not in markers]
    if len(missing) != 1: return markers, None
    missing_key = missing[0]

    if missing_key == 'BR':
        new_pt = np.array(markers['BL']) + (np.array(markers['TR']) - np.array(markers['TL']))
        markers['BR'] = tuple(new_pt)
    elif missing_key == 'BL':
        new_pt = np.array(markers['BR']) + (np.array(markers['TL']) - np.array(markers['TR']))
        markers['BL'] = tuple(new_pt)
    elif missing_key == 'TR':
        new_pt = np.array(markers['TL']) + (np.array(markers['BR']) - np.array(markers['BL']))
        markers['TR'] = tuple(new_pt)
    elif missing_key == 'TL':
        new_pt = np.array(markers['TR']) + (np.array(markers['BL']) - np.array(markers['BR']))
        markers['TL'] = tuple(new_pt)
    return markers, missing_key


class SmartScanner:
    def __init__(self):
        print(f"[INIT] Start kamery: {CAMERA_INDEX}")
        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)

        # Wysoka rozdzielczość
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 4160)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 3120)
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)

        self.setup_directories()
        self.init_firestore()
        self.detector = cv2.QRCodeDetector()

        # Zmienne stanu sesji
        self.current_user_id = None
        self.current_client_name = ""
        self.current_user_scans = 0
        self.last_qr_data = ""
        self.qr_cooldown = 0
        self.last_activity_time = 0

        # Zmienne stanu skanowania
        self.timer_start = None
        self.success_timer_start = None
        self.error_timer_start = None
        self.scan_abort_count = 0  # Licznik przerwań skanowania (reset focusu)

        self.last_valid_markers = {}
        self.marker_history = {}

        self.ui_state = {
            "status_header": "ZABLOKOWANY",
            "status_sub": "Zeskanuj bilet",
            "bg_color": "blue",
            "progress": 0.0,
            "user_name": "",
            "scan_count": 0,
            "max_scans": MAX_SCANS,
            "is_logged_in": False
        }

    def setup_directories(self):
        for path in [OUTPUT_RAW_DIR, OUTPUT_CROPPED_DIR, EXTRA_OUTPUT_ROOT]:
            os.makedirs(path, exist_ok=True)

    def remove_accents(self, text):
        replacements = {
            'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
            'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N', 'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
        }
        clean_text = str(text)
        for k, v in replacements.items():
            clean_text = clean_text.replace(k, v)
        return clean_text

    def init_firestore(self):
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(FIREBASE_KEY_PATH)
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("[DB] Polaczono.")
        except Exception as e:
            print(f"[DB ERR] {e}")

    def update_ui(self):
        socketio.emit('ui_update', self.ui_state)

    def set_status(self, header, sub, color):
        if (self.ui_state["status_header"] != header or
                self.ui_state["bg_color"] != color):
            self.ui_state.update({
                "status_header": header,
                "status_sub": sub,
                "bg_color": color
            })
            self.update_ui()
            safe_header = self.remove_accents(header)
            # print(f"[UI] {safe_header} | {color}")

    def trigger_focus_reset(self):
        """Metoda uruchamiana w wątku: wymusza mechaniczny reset focusu."""
        try:
            print(f"[FOCUS] Wykryto problemy ({self.scan_abort_count} przerwań). RESETOWANIE SOCZEWKI...")

            # Informacja dla użytkownika (opcjonalnie, lub zostawiamy 'POZIOMUJ')
            # self.set_status("KALIBRACJA...", "Poprawiam ostrość kamery...", "orange")

            # 1. Wyłącz autofocus
            self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)

            # 2. Przesuń soczewkę na pozycję 0 (Macro) - to zmusza mechanizm do ruchu
            self.cap.set(cv2.CAP_PROP_FOCUS, 0)

            # 3. Odczekaj chwilę, aż hardware zareaguje
            time.sleep(0.3)

            # 4. Włącz autofocus ponownie
            self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)

            # Reset licznika, żeby dać szansę kamerze na ustawienie się
            self.scan_abort_count = 0
            print("[FOCUS] Reset zakończony.")

        except Exception as e:
            print(f"[FOCUS ERR] Nie udało się zresetować: {e}")

    # --- LOGIKA BIZNESOWA ---

    def get_user_data(self, uuid):
        try:
            doc = self.db.collection(COLLECTION_NAME).document(uuid).get()
            if doc.exists:
                d = doc.to_dict()
                client_name = d.get("ClientName", "Gość") or "Gość"
                return True, d.get("StainedGlass", {}).get("ScanCount", 0), client_name
            return False, 0, ""
        except:
            return False, 0, ""

    def handle_login_scan(self, qr_data):
        curr = time.time()
        if qr_data == self.last_qr_data and (curr - self.qr_cooldown < 5.0): return

        self.last_qr_data = qr_data
        self.qr_cooldown = curr
        clean_uuid = qr_data.split("/")[-1].strip()
        print(f"[LOGIN] Sprawdzam UUID: {clean_uuid}")

        exists, count, name = self.get_user_data(clean_uuid)

        if exists:
            if count >= MAX_SCANS:
                safe_name = self.remove_accents(name)
                print(f"[LOGIN] Limit wyczerpany dla {safe_name}")
                self.error_timer_start = time.time()
                self.set_status("LIMIT WYCZERPANY", "Brak dostępnych skanów", "red")
                return

            self.error_timer_start = None
            self.success_timer_start = None

            # RESETUJEMY licznik błędów przy nowym logowaniu
            self.scan_abort_count = 0

            self.current_user_id = clean_uuid
            self.current_user_scans = count
            self.current_client_name = name

            self.ui_state.update({
                "user_name": name,
                "scan_count": count,
                "is_logged_in": True
            })
            self.last_activity_time = time.time()
            self.set_status(f"Cześć {name}!", "Umieść rysunek pod skanerem", "green")

            safe_name = self.remove_accents(name)
            print(f"[LOGIN] Zalogowano: {safe_name}")
        else:
            self.error_timer_start = time.time()
            self.set_status("BŁĄD", "Nieznany bilet", "red")

    # --- PROCESOWANIE OBRAZU W TLE ---

    def process_and_save_task(self, frame, markers, user_id, scan_nr):
        filename = f"{user_id}_{scan_nr}.jpg"
        print(f"[PROCESS] Rozpoczynam przetwarzanie {filename}...")

        try:
            # 1. Zapis RAW do folderu zbiorczego (backup)
            path_raw_backup = os.path.join(OUTPUT_RAW_DIR, filename)
            cv2.imwrite(path_raw_backup, frame)

            # 2. Logika geometryczna
            required = ['TL', 'TR', 'BR', 'BL']
            found_count = len([k for k in required if k in markers])
            final_markers = markers.copy()

            if found_count == 3:
                final_markers, _ = estimate_missing_point(final_markers)
            elif found_count < 3:
                print(f"[PROCESS ERR] Za malo punktow ({found_count})")
                return

            src_pts = np.array([final_markers['TL'], final_markers['TR'], final_markers['BR'], final_markers['BL']],
                               dtype="float32")

            width_a = distance(final_markers['TL'], final_markers['TR'])
            width_b = distance(final_markers['BL'], final_markers['BR'])
            max_width = max(int(width_a), int(width_b))

            height_a = distance(final_markers['TL'], final_markers['BL'])
            height_b = distance(final_markers['TR'], final_markers['BR'])
            max_height = max(int(height_a), int(height_b))

            dst_pts = np.array([
                [0, 0],
                [max_width - 1, 0],
                [max_width - 1, max_height - 1],
                [0, max_height - 1]
            ], dtype="float32")

            M = cv2.getPerspectiveTransform(src_pts, dst_pts)
            warped_img = cv2.warpPerspective(frame, M, (max_width, max_height))

            # 3. Rotacja i Resize
            warped_img = cv2.rotate(warped_img, cv2.ROTATE_90_CLOCKWISE)
            warped_img = cv2.resize(warped_img, (FINAL_WIDTH, FINAL_HEIGHT), interpolation=cv2.INTER_AREA)

            # 4. Zapis wyników
            # A: Zapis do folderu zbiorczego CROPPED
            path_cropped = os.path.join(OUTPUT_CROPPED_DIR, filename)
            cv2.imwrite(path_cropped, warped_img)

            # B: Zapis do full_content (Dla Electrona / Uploader)
            folder_name = os.path.splitext(filename)[0]
            path_extra_dir = os.path.join(EXTRA_OUTPUT_ROOT, folder_name)

            os.makedirs(path_extra_dir, exist_ok=True)

            path_extra_scan = os.path.join(path_extra_dir, "scan.jpg")
            path_extra_raw = os.path.join(path_extra_dir, "raw.jpg")

            cv2.imwrite(path_extra_scan, warped_img)
            cv2.imwrite(path_extra_raw, frame)

            # 5. Aktualizacja Firestore
            self.db.collection(COLLECTION_NAME).document(user_id) \
                .update({"StainedGlass.ScanCount": firestore.Increment(1)})

            print(f"[PROCESS OK] Zapisano w full_content: scan.jpg i raw.jpg")

        except Exception as e:
            print(f"[PROCESS CRITICAL ERROR] {e}")

    def trigger_scan_procedure(self, frame, markers):
        print("[PHOTO] Trigger! Uruchamiam proces w tle...")

        scan_nr = self.current_user_scans + 1
        user_id = self.current_user_id

        self.current_user_scans += 1
        self.last_activity_time = time.time()
        self.scan_abort_count = 0  # Sukces! Zerujemy licznik błędów

        self.ui_state["scan_count"] = self.current_user_scans

        t = threading.Thread(target=self.process_and_save_task,
                             args=(frame.copy(), markers, user_id, scan_nr))
        t.start()

        self.success_timer_start = time.time()
        self.set_status("GOTOWE!", "Rysunek dodany do kolejki! Obserwuj instalację", "blue")

    # --- GLOWNA PETLA VIDEO ---

    def generate_frames(self):
        print("[LOOP] Start petli...")
        while True:
            socketio.sleep(0.01)

            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.5)
                continue

            display_frame = frame.copy()
            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # --- KROK 1: SUROWA DETEKCJA ---
            found_long_codes = []
            raw_frame_markers = {}

            try:
                retval, decoded, points, _ = self.detector.detectAndDecodeMulti(gray_frame)

                if retval:
                    if points is not None:
                        points = points.astype(int)

                    for i, data in enumerate(decoded):
                        if not data: continue

                        if points is not None and len(points) > i:
                            pts = points[i]
                            center_x = np.mean(pts[:, 0])
                            center_y = np.mean(pts[:, 1])

                            # Klasyfikacja
                            if len(data) > 20:
                                found_long_codes.append(data)

                            elif "_" in data:
                                # ZNACZNIKI (TL, TR etc.)
                                parts = data.split('_')
                                pos = parts[0]
                                if pos in ['TL', 'TR', 'BL', 'BR']:
                                    raw_frame_markers[pos] = (center_x, center_y)

                # Obsługa logowania
                for code in found_long_codes:
                    self.handle_login_scan(code)
                    break

            except Exception as e:
                pass

            # --- KROK 2: STABILIZACJA (PAMIĘĆ) ---
            current_time = time.time()

            for pos, coord in raw_frame_markers.items():
                self.marker_history[pos] = {
                    'coord': coord,
                    'seen': current_time
                }

            stabilized_markers = {}
            for pos in ['TL', 'TR', 'BL', 'BR']:
                if pos in self.marker_history:
                    last_data = self.marker_history[pos]
                    if current_time - last_data['seen'] < MARKER_MEMORY_DURATION:
                        stabilized_markers[pos] = last_data['coord']

            # --- OBSŁUGA BLOKAD ---
            is_blocked = False

            if self.error_timer_start:
                if time.time() - self.error_timer_start < ERROR_DURATION:
                    is_blocked = True
                else:
                    self.error_timer_start = None
                    self.set_status("ZABLOKOWANY", "Zeskanuj bilet", "blue")

            if self.success_timer_start:
                if time.time() - self.success_timer_start < SUCCESS_DURATION:
                    is_blocked = True
                    self.set_status("GOTOWE!", "Rysunek dodany do kolejki! Obserwuj instalację", "blue")
                else:
                    self.success_timer_start = None
                    self.current_user_id = None
                    self.ui_state["is_logged_in"] = False
                    self.set_status("ZABLOKOWANY", "Zeskanuj bilet", "blue")

            if is_blocked:
                try:
                    ret, buffer = cv2.imencode('.jpg', display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
                    yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                except:
                    pass
                continue

            # --- LOGIKA SKANOWANIA ---
            if self.current_user_id:
                if time.time() - self.last_activity_time > SESSION_TIMEOUT:
                    self.current_user_id = None
                    self.ui_state["is_logged_in"] = False
                    self.set_status("WYLOGOWANO", "Czas minął", "blue")
                    continue

                unique_markers_count = len(stabilized_markers)

                if unique_markers_count >= MIN_QR_COUNT:
                    if self.timer_start is None:
                        self.timer_start = time.time()
                        print(f"[TRIGGER] Start odliczania!")

                    elapsed = time.time() - self.timer_start
                    progress = min(elapsed / TRIGGER_TIME, 1.0)
                    self.ui_state["progress"] = progress

                    left = TRIGGER_TIME - elapsed
                    self.set_status("SKANOWANIE...", f"{left:.1f}s", "green")
                    self.update_ui()

                    self.last_valid_markers = stabilized_markers.copy()

                    if elapsed >= TRIGGER_TIME:
                        self.trigger_scan_procedure(frame, self.last_valid_markers)
                        self.timer_start = None
                        self.ui_state["progress"] = 0.0
                else:
                    # --- TUTAJ JEST LOGIKA RESETU FOCUSU (WATCHDOG) ---

                    # Jeśli timer był aktywny (czyli skanowaliśmy), a teraz go kasujemy -> TO JEST PRZERWANIE
                    if self.timer_start is not None:
                        self.timer_start = None
                        self.ui_state["progress"] = 0.0
                        self.set_status("POZIOMUJ...", "Zgubiono znaczniki", "orange")

                        self.scan_abort_count += 1
                        print(f"[SKAN] Przerwano! Próba: {self.scan_abort_count}/{MAX_ABORTS_BEFORE_RESET}")

                        if self.scan_abort_count > MAX_ABORTS_BEFORE_RESET:
                            # Uruchamiamy reset w tle, aby nie zamrozić podglądu
                            threading.Thread(target=self.trigger_focus_reset).start()

                    # Standardowe komunikaty statusu
                    if unique_markers_count == 0:
                        self.set_status(f"Cześć {self.current_client_name}!", "Połóż kartkę pod skanerem", "green")
                    else:
                        self.set_status("POZIOMUJ...", f"Widzę znaczników: {unique_markers_count}/4", "orange")
            else:
                if self.ui_state["bg_color"] != "blue":
                    self.set_status("ZABLOKOWANY", "Zeskanuj bilet", "blue")
                    self.ui_state["is_logged_in"] = False

            try:
                ret, buffer = cv2.imencode('.jpg', display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
                yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            except:
                pass


scanner = SmartScanner()


@socketio.on('connect')
def handle_connect():
    print("[SOCKET] Klient polaczony.")
    scanner.update_ui()


@app.route('/video_feed')
def video_feed():
    return Response(scanner.generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)