import cv2
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import time
import sys
import os
import datetime
from PIL import Image, ImageDraw, ImageFont

# --- KONFIGURACJA OGÓLNA ---
CAMERA_INDEX = 2
FIREBASE_KEY_PATH = "serviceAccountKey.json"
COLLECTION_NAME = "qr_codes"
OUTPUT_DIR = os.path.join(os.getcwd(), "content", "RAW_PHOTO")

# --- KONFIGURACJA FONTU ---
FONT_PATH = "C:/Windows/Fonts/Arial.ttf"
FONT_SIZE_HEADER = 32
FONT_SIZE_NORMAL = 20
FONT_SIZE_SMALL = 14

# --- PARAMETRY ---
BRIGHTNESS_THRESHOLD = 100
MIN_COVERAGE_PERCENT = 5.0
CHANGE_THRESHOLD_FOR_RESET = 10.0
TRIGGER_TIME = 0.5
MIN_QR_COUNT = 3
SESSION_TIMEOUT = 181

# --- LIMIT SKANÓW ---
MAX_SCANS = 5

# UI SETTINGS
WINDOW_NAME = "System Wesola - Skaner PRO"
SIDEBAR_WIDTH = 400
DISPLAY_HEIGHT = 700

# WYMUSZONA ROZDZIELCZOŚĆ
TARGET_WIDTH = 1280
TARGET_HEIGHT = 960


class SmartScanner:
    def __init__(self):
        print(f"--- START SYSTEMU (Kamera ID: {CAMERA_INDEX}) ---")
        print(f"--- Limit skanow: {MAX_SCANS} na uzytkownika ---")

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.init_firestore()
        self.init_fonts()

        self.current_user_id = None
        self.current_client_name = ""  # <--- NOWA ZMIENNA NA IMIĘ
        self.current_user_scans = 0

        self.last_qr_data = ""
        self.qr_cooldown = 0
        self.last_activity_time = 0

        self.timer_start = None
        self.is_locked = False
        self.capture_coverage_level = 0.0

        self.status_text = ["ZABLOKOWANY", "Zeskanuj bilet"]
        self.status_color = (0, 0, 255)  # BGR
        self.ui_bg_color = (40, 40, 40)
        self.feedback_timer = 0
        self.progress_bar_val = 0.0

        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            print(f"[BLAD] Brak kamery ID {CAMERA_INDEX}")
            sys.exit(1)

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, TARGET_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TARGET_HEIGHT)
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)

        real_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        real_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

        if int(real_w) != TARGET_WIDTH or int(real_h) != TARGET_HEIGHT:
            print(f"[INFO] Skalowanie programowe aktywne.")

        self.detector = cv2.QRCodeDetector()
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    def init_firestore(self):
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(FIREBASE_KEY_PATH)
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
        except Exception as e:
            print(f"[ERR] Firestore: {e}")
            sys.exit(1)

    def init_fonts(self):
        """Ładuje fonty do pamięci"""
        try:
            self.font_header = ImageFont.truetype(FONT_PATH, FONT_SIZE_HEADER)
            self.font_normal = ImageFont.truetype(FONT_PATH, FONT_SIZE_NORMAL)
            self.font_small = ImageFont.truetype(FONT_PATH, FONT_SIZE_SMALL)
        except IOError:
            print(f"[WARN] Nie znaleziono fontu {FONT_PATH}. Używam domyślnego.")
            self.font_header = ImageFont.load_default()
            self.font_normal = ImageFont.load_default()
            self.font_small = ImageFont.load_default()

    def get_white_coverage(self, frame):
        small = cv2.resize(frame, (320, 240))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, BRIGHTNESS_THRESHOLD, 255, cv2.THRESH_BINARY)
        white_pixels = cv2.countNonZero(mask)
        total_pixels = small.shape[0] * small.shape[1]
        pct = (white_pixels / total_pixels) * 100.0
        return pct

    def get_user_data(self, uuid):
        try:
            doc_ref = self.db.collection(COLLECTION_NAME).document(uuid)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()

                # 1. Pobieramy licznik
                stained_glass = data.get("StainedGlass", {})
                scan_count = stained_glass.get("ScanCount", 0)

                # 2. Pobieramy ClientName (jeśli brak, wpisujemy 'Gość')
                client_name = data.get("ClientName", "")
                if not client_name:
                    client_name = "Gość"

                return True, scan_count, client_name
            return False, 0, ""
        except Exception as e:
            print(f"[ERR] Błąd pobierania danych: {e}")
            return False, 0, ""

    def trigger_ui_feedback(self, mode):
        duration = 0.5
        if mode == "login":
            self.ui_bg_color = (255, 0, 0)
        elif mode == "error":
            self.ui_bg_color = (0, 0, 255)
        elif mode == "photo":
            self.ui_bg_color = (0, 255, 0)
        elif mode == "logout":
            self.ui_bg_color = (0, 165, 255)
            duration = 3.0

        self.feedback_timer = time.time() + duration

    def check_session_timeout(self):
        if self.current_user_id:
            elapsed = time.time() - self.last_activity_time
            if elapsed > SESSION_TIMEOUT:
                print(f"[AUTO-LOGOUT] Przekroczono czas {SESSION_TIMEOUT}s")
                self.current_user_id = None
                self.current_client_name = ""  # <--- RESET IMIENIA
                self.status_text = ["WYLOGOWANO", "Czas minal"]
                self.status_color = (0, 165, 255)
                self.trigger_ui_feedback("logout")

    def handle_login_scan(self, qr_data):
        curr_time = time.time()
        if qr_data == self.last_qr_data and (curr_time - self.qr_cooldown < 2.0):
            return

        self.last_qr_data = qr_data
        self.qr_cooldown = curr_time

        clean_uuid = qr_data.split("/")[-1].strip()
        print(f"[LOGIN] Sprawdzam: {clean_uuid}")

        # Pobieramy dane z nową sygnaturą funkcji (3 wartości)
        exists, scan_count, client_name = self.get_user_data(clean_uuid)

        if exists:
            self.current_user_id = clean_uuid
            self.current_user_scans = scan_count
            self.current_client_name = client_name  # <--- ZAPISANIE IMIENIA

            self.is_locked = False
            self.timer_start = None
            self.last_activity_time = time.time()
            self.trigger_ui_feedback("login")
            print(f"[LOGIN] Zalogowano: {client_name} ({clean_uuid}) | Skanów: {scan_count}")
        else:
            self.trigger_ui_feedback("error")

    def process_camera_logic(self, clean_frame, art_qr_count):
        if self.current_user_scans >= MAX_SCANS:
            self.status_text = ["LIMIT WYCZERPANY", f"Wykonano: {MAX_SCANS}/{MAX_SCANS}"]
            self.status_color = (0, 0, 255)
            self.progress_bar_val = 0.0
            return

        current_time = time.time()
        coverage_pct = self.get_white_coverage(clean_frame)
        paper_detected = coverage_pct > MIN_COVERAGE_PERCENT

        if self.is_locked:
            diff = abs(coverage_pct - self.capture_coverage_level)
            if diff > CHANGE_THRESHOLD_FOR_RESET:
                self.is_locked = False
                print(f"[RESET] System odblokowany")
            else:
                self.status_text = ["ZAPISANO!", "Zmień kartkę"]
                self.status_color = (0, 255, 255)
        else:
            if paper_detected and art_qr_count >= MIN_QR_COUNT:
                if self.timer_start is None:
                    self.timer_start = current_time

                elapsed = current_time - self.timer_start
                self.progress_bar_val = min(elapsed / TRIGGER_TIME, 1.0)

                if elapsed >= TRIGGER_TIME:
                    self.take_photo(clean_frame, coverage_pct)
                else:
                    self.status_text = ["TRZYMAJ...", f"{((TRIGGER_TIME - elapsed)):.1f}s"]
                    self.status_color = (0, 255, 0)
            else:
                self.timer_start = None
                self.progress_bar_val = 0.0
                if not paper_detected:
                    self.status_text = ["GOTOWY", "Poloz kartke"]
                else:
                    self.status_text = ["POZIOMUJ...", f"QR: {art_qr_count}/3"]
                self.status_color = (255, 255, 255)

    def take_photo(self, clean_frame, coverage_pct):
        print(f"[CAM] FOTO WYKONANE! Rozmiar: {clean_frame.shape[1]}x{clean_frame.shape[0]}")

        current_scan_nr = self.current_user_scans + 1
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"scan_{self.current_user_id}_{current_scan_nr}_{timestamp}.jpg"

        filepath = os.path.join(OUTPUT_DIR, filename)

        try:
            cv2.imwrite(filepath, clean_frame)
            print(f"[DISK] Zapisano: {filename}")

            self.current_user_scans += 1
            user_ref = self.db.collection(COLLECTION_NAME).document(self.current_user_id)
            user_ref.update({"StainedGlass.ScanCount": firestore.Increment(1)})
            print(f"[CLOUD] Zaktualizowano StainedGlass.ScanCount: {self.current_user_scans}")

        except Exception as e:
            print(f"[ERR] Blad zapisu: {e}")

        self.capture_coverage_level = coverage_pct
        self.is_locked = True
        self.timer_start = None
        self.progress_bar_val = 0.0
        self.last_activity_time = time.time()
        self.trigger_ui_feedback("photo")

    def draw_ui(self, display_frame):
        h, w = display_frame.shape[:2]
        if time.time() > self.feedback_timer:
            self.ui_bg_color = (40, 40, 40)

        # 1. Tworzymy sidebar w OpenCV
        sidebar = np.zeros((h, SIDEBAR_WIDTH, 3), dtype=np.uint8)
        sidebar[:] = self.ui_bg_color

        # 2. Paski postępu w OpenCV
        if self.progress_bar_val > 0:
            bar_w = int((SIDEBAR_WIDTH - 40) * self.progress_bar_val)
            cv2.rectangle(sidebar, (20, 350), (20 + bar_w, 380), (0, 255, 0), -1)
            cv2.rectangle(sidebar, (20, 350), (SIDEBAR_WIDTH - 20, 380), (255, 255, 255), 2)

        # 3. Konwersja Sidebar na PIL Image
        sidebar_pil = Image.fromarray(cv2.cvtColor(sidebar, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(sidebar_pil)

        def bgr_to_rgb(bgr_tuple):
            return bgr_tuple[::-1]

        # --- RYSOWANIE TEKSTU (PIL) ---

        # Główny status
        draw.text((20, 70), self.status_text[0], font=self.font_header, fill=bgr_to_rgb(self.status_color))
        draw.text((20, 120), self.status_text[1], font=self.font_normal, fill=(200, 200, 200))

        if self.current_user_id:
            # --- ZMIANA: Wyświetlanie Imienia zamiast ID ---
            welcome_msg = f"Cześć! {self.current_client_name}"
            draw.text((20, 230), welcome_msg, font=self.font_normal, fill=(0, 255, 0))

            scans_color = (0, 255, 0) if self.current_user_scans < MAX_SCANS else (0, 0, 255)
            scan_info = f"Skanow: {self.current_user_scans} / {MAX_SCANS}"
            draw.text((20, 260), scan_info, font=self.font_header, fill=bgr_to_rgb(scans_color))

            time_left = max(0, int(SESSION_TIMEOUT - (time.time() - self.last_activity_time)))
            draw.text((20, 310), f"Czas sesji: {time_left}s", font=self.font_normal, fill=(200, 200, 200))
        else:
            draw.text((20, 230), "NIEZALOGOWANY", font=self.font_normal, fill=(100, 100, 100))

        # Stopka usunięta zgodnie z życzeniem (zakomentowana w Twoim kodzie, tu usunięta wizualnie)

        # 4. Konwersja powrotna PIL -> OpenCV
        sidebar = cv2.cvtColor(np.array(sidebar_pil), cv2.COLOR_RGB2BGR)

        return np.hstack((display_frame, sidebar))

    def run(self):
        while True:
            ret, frame = self.cap.read()
            if not ret: continue

            display_frame = frame.copy()

            self.check_session_timeout()

            try:
                retval, decoded, points, _ = self.detector.detectAndDecodeMulti(frame)
            except:
                retval = False

            decoded_list = decoded if retval else []
            qr_points = points if retval else []

            login_code_found = None
            art_qr_count = 0

            for i, data in enumerate(decoded_list):
                if not data: continue

                pts = qr_points[i].astype(int)
                for j in range(4):
                    cv2.line(display_frame, tuple(pts[j]), tuple(pts[(j + 1) % 4]), (0, 255, 0), 4)

                if len(data) > 20:
                    login_code_found = data
                elif "_" in data and len(data) < 10:
                    art_qr_count += 1

            if login_code_found:
                self.handle_login_scan(login_code_found)

            if self.current_user_id:
                self.process_camera_logic(frame, art_qr_count)
            else:
                if time.time() > self.feedback_timer:
                    self.status_text = ["ZABLOKOWANY", "Pokaz bilet"]
                    self.status_color = (0, 0, 255)
                self.progress_bar_val = 0.0

            orig_h, orig_w = display_frame.shape[:2]
            scale = DISPLAY_HEIGHT / orig_h
            new_w = int(orig_w * scale)
            preview = cv2.resize(display_frame, (new_w, DISPLAY_HEIGHT))

            final_img = self.draw_ui(preview)
            cv2.imshow(WINDOW_NAME, final_img)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        self.cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    app = SmartScanner()
    app.run()