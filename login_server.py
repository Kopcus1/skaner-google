# Zapisz to jako login.py (zastąp obecny kod, ale zrób kopię!)
import cv2
import firebase_admin
from firebase_admin import credentials, firestore
import time
import os
import datetime
from flask import Flask, Response
from flask_socketio import SocketIO

# --- KONFIGURACJA SERWERA ---
app = Flask(__name__)
# cors_allowed_origins="*" pozwala Electronowi łączyć się bez błędów
socketio = SocketIO(app, cors_allowed_origins="*")

# --- TWOJE STAŁE ---
CAMERA_INDEX = 2  # Zostawiam Twoje ID kamery
FIREBASE_KEY_PATH = "serviceAccountKey.json"
COLLECTION_NAME = "qr_codes"
OUTPUT_DIR = os.path.join(os.getcwd(), "content", "RAW_PHOTO")
EXTRA_OUTPUT_ROOT = os.path.join(os.getcwd(), "full_content")

# UI Settings - logika
BRIGHTNESS_THRESHOLD = 100
MIN_COVERAGE_PERCENT = 5.0
TRIGGER_TIME = 3
MIN_QR_COUNT = 4
SESSION_TIMEOUT = 181
MAX_SCANS = 5


class SmartScanner:
    def __init__(self):
        # Inicjalizacja jak dawniej
        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 960)
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)

        self.init_firestore()
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.detector = cv2.QRCodeDetector()

        # Zmienne stanu
        self.current_user_id = None
        self.current_client_name = ""
        self.current_user_scans = 0
        self.last_qr_data = ""
        self.qr_cooldown = 0
        self.last_activity_time = 0
        self.timer_start = None
        self.ui_state = {
            "status_header": "ZABLOKOWANY",
            "status_sub": "Zeskanuj bilet",
            "bg_color": "blue",
            "progress": 0.0,
            "user_name": "",
            "scan_count": 0,
            "is_logged_in": False
        }

    def init_firestore(self):
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()

    def get_white_coverage(self, frame):
        small = cv2.resize(frame, (320, 240))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, BRIGHTNESS_THRESHOLD, 255, cv2.THRESH_BINARY)
        return (cv2.countNonZero(mask) / (small.shape[0] * small.shape[1])) * 100.0

    def get_user_data(self, uuid):
        try:
            doc = self.db.collection(COLLECTION_NAME).document(uuid).get()
            if doc.exists:
                d = doc.to_dict()
                return True, d.get("StainedGlass", {}).get("ScanCount", 0), d.get("ClientName", "Gość")
            return False, 0, ""
        except:
            return False, 0, ""

    def emit_ui(self):
        socketio.emit('ui_update', self.ui_state)

    def update_status(self, header, sub, color):
        self.ui_state.update({"status_header": header, "status_sub": sub, "bg_color": color})
        self.emit_ui()

    def handle_login_scan(self, qr_data):
        curr = time.time()
        if qr_data == self.last_qr_data and (curr - self.qr_cooldown < 2.0): return
        self.last_qr_data = qr_data
        self.qr_cooldown = curr

        clean_uuid = qr_data.split("/")[-1].strip()
        exists, count, name = self.get_user_data(clean_uuid)

        if exists:
            if count >= MAX_SCANS:
                self.update_status("LIMIT WYCZERPANY", "Brak skanów", "red")
                return

            self.current_user_id = clean_uuid
            self.current_user_scans = count
            self.current_client_name = name
            self.ui_state.update({"user_name": name, "scan_count": count, "is_logged_in": True})
            self.last_activity_time = time.time()
            self.update_status(f"Cześć {name}!", "Przygotuj rysunek", "green")
        else:
            self.update_status("BŁĄD", "Nieznany bilet", "red")

    def take_photo(self, frame):
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        scan_nr = self.current_user_scans + 1
        filename = f"{self.current_user_id}_{scan_nr}_{ts}.jpg"

        # Zapis 1: RAW
        path_std = os.path.join(OUTPUT_DIR, filename)
        cv2.imwrite(path_std, frame)

        # Zapis 2: Extra folder (z Twojego kodu)
        folder_name = os.path.splitext(filename)[0]
        extra_path = os.path.join(EXTRA_OUTPUT_ROOT, folder_name)
        os.makedirs(extra_path, exist_ok=True)
        cv2.imwrite(os.path.join(extra_path, filename), frame)

        # Firestore Update
        try:
            self.db.collection(COLLECTION_NAME).document(self.current_user_id) \
                .update({"StainedGlass.ScanCount": firestore.Increment(1)})
            self.current_user_scans += 1
        except Exception as e:
            print(f"Cloud Error: {e}")

        # Logout
        self.current_user_id = None
        self.ui_state["is_logged_in"] = False
        self.update_status("GOTOWE!", "Zapisano pomyślnie", "blue")
        time.sleep(2)  # Chwila na pokazanie komunikatu sukcesu
        self.update_status("ZABLOKOWANY", "Zeskanuj bilet", "blue")

    def generate_frames(self):
        while True:
            ret, frame = self.cap.read()
            if not ret: continue

            if self.current_user_id:
                if time.time() - self.last_activity_time > SESSION_TIMEOUT:
                    self.current_user_id = None
                    self.ui_state["is_logged_in"] = False
                    self.update_status("WYLOGOWANO", "Czas minął", "blue")

                pct = self.get_white_coverage(frame)
                if pct > MIN_COVERAGE_PERCENT:
                    if self.timer_start is None: self.timer_start = time.time()
                    elapsed = time.time() - self.timer_start
                    self.ui_state["progress"] = min(elapsed / TRIGGER_TIME, 1.0)
                    self.emit_ui()

                    if elapsed >= TRIGGER_TIME:
                        self.take_photo(frame)
                        self.timer_start = None
                        self.ui_state["progress"] = 0.0
                else:
                    self.timer_start = None
                    self.ui_state["progress"] = 0.0
                    self.emit_ui()
            else:
                # Logika QR
                try:
                    retval, decoded, _, _ = self.detector.detectAndDecodeMulti(frame)
                    if retval:
                        for data in decoded:
                            if len(data) > 20: self.handle_login_scan(data)
                except:
                    pass

            # Kompresja JPEG dla Electrona
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


scanner = SmartScanner()


@app.route('/video_feed')
def video_feed():
    return Response(scanner.generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


if __name__ == '__main__':
    print("--- START SERWERA FLASK (GUI w Electron) ---")
    socketio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)