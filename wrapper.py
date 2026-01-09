import cv2
import numpy as np
import json
import os
import shutil
import time
import sys

# --- KONFIGURACJA ŚCIEŻEK ---
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "content")

# WEJŚCIE: Tu trafiają wyprostowane skany (z processor_dewarp.py)
INPUT_FOLDER = os.path.join(BASE_DIR, "CROPPED")

# WYJŚCIE: Gotowy obrazek tła z wklejonym rysunkiem (dla AI)
OUTPUT_FOLDER = os.path.join(BASE_DIR, "AI_INPUT")

# ARCHIWUM: Tu trafiają skany po nałożeniu na okno
ARCHIVE_FOLDER = os.path.join(BASE_DIR, "CROPPED_ARCHIVE")

# PLIKI KONFIGURACYJNE (W głównym folderze)
CONFIG_FILE = "full_map_config.json"
BACKGROUND_FILE = "window_mapper_window.jpg"


class WindowWarper:
    def __init__(self):
        self.ensure_dirs()
        self.load_config()
        self.check_background()

    def ensure_dirs(self):
        for path in [INPUT_FOLDER, OUTPUT_FOLDER, ARCHIVE_FOLDER]:
            if not os.path.exists(path):
                os.makedirs(path)
                print(f"[INIT] Utworzono folder: {path}")

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            print(f"[CRITICAL] Brak pliku {CONFIG_FILE}!")
            print("Uruchom najpierw narzędzie do kalibracji/mapowania.")
            sys.exit(1)

        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                self.mapping = data['mapping']
                self.target_w = data.get('target_w', 1920)
                self.target_h = data.get('target_h', 1080)
            print(f"[CONFIG] Zaladowano {len(self.mapping)} stref mapowania.")
            print(f"[CONFIG] Rozdzielczosc docelowa: {self.target_w}x{self.target_h}")
        except Exception as e:
            print(f"[ERROR] Blad odczytu configu: {e}")
            sys.exit(1)

    def check_background(self):
        if not os.path.exists(BACKGROUND_FILE):
            print(f"[WARN] Brak pliku tla {BACKGROUND_FILE}!")
            print("Tlo bedzie czarne.")
        else:
            print(f"[BG] Wykryto tlo: {BACKGROUND_FILE}")

    def get_four_corners(self, pts):
        """Redukuje punkty wielokąta (np. łuku) do 4 narożników dla perspektywy."""
        pts = np.array(pts, dtype="float32")
        if len(pts) == 4:
            return pts

        # Logika dla 5 punktów (łuk) - usuwamy szczyt
        indices = np.argsort(pts[:, 1])
        top_indices = indices[:3]
        top_pts = pts[top_indices]
        top_x_indices = np.argsort(top_pts[:, 0])
        index_in_top = top_x_indices[1]
        idx_to_remove = top_indices[index_in_top]

        return np.delete(pts, idx_to_remove, axis=0)

    def order_points(self, pts):
        # Sortowanie: TL, TR, BR, BL
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]  # TL
        rect[2] = pts[np.argmax(s)]  # BR
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]  # TR
        rect[3] = pts[np.argmax(diff)]  # BL
        return rect

    def process_file(self, filename):
        src_path = os.path.join(INPUT_FOLDER, filename)

        # Oczekujemy na zwolnienie pliku przez poprzedni proces
        time.sleep(0.5)

        img_src = cv2.imread(src_path)
        if img_src is None:
            print(f"[SKIP] Nie mozna otworzyc: {filename}")
            return False

        # 1. Przygotuj Tło (Canvas)
        if os.path.exists(BACKGROUND_FILE):
            canvas = cv2.imread(BACKGROUND_FILE)
            if canvas.shape[1] != self.target_w or canvas.shape[0] != self.target_h:
                canvas = cv2.resize(canvas, (self.target_w, self.target_h))
        else:
            canvas = np.zeros((self.target_h, self.target_w, 3), dtype=np.uint8)

        # 2. Mapowanie Stref
        for zone in self.mapping:
            src_pts_raw = zone['source_points']
            dst_pts_raw = zone['target_points']

            # Geometria
            src_rect = self.order_points(self.get_four_corners(src_pts_raw))
            dst_rect = self.order_points(self.get_four_corners(dst_pts_raw))

            # Wymiary patcha
            widthA = np.sqrt(((dst_rect[0][0] - dst_rect[1][0]) ** 2) + ((dst_rect[0][1] - dst_rect[1][1]) ** 2))
            widthB = np.sqrt(((dst_rect[2][0] - dst_rect[3][0]) ** 2) + ((dst_rect[2][1] - dst_rect[3][1]) ** 2))
            maxWidth = max(int(widthA), int(widthB))

            heightA = np.sqrt(((dst_rect[0][0] - dst_rect[3][0]) ** 2) + ((dst_rect[0][1] - dst_rect[3][1]) ** 2))
            heightB = np.sqrt(((dst_rect[1][0] - dst_rect[2][0]) ** 2) + ((dst_rect[1][1] - dst_rect[2][1]) ** 2))
            maxHeight = max(int(heightA), int(heightB))

            # Transformacja 1: Wycięcie ze źródła (Warp)
            dst_flat = np.array([
                [0, 0],
                [maxWidth - 1, 0],
                [maxWidth - 1, maxHeight - 1],
                [0, maxHeight - 1]
            ], dtype="float32")

            try:
                M = cv2.getPerspectiveTransform(src_rect, dst_flat)
                warped_patch = cv2.warpPerspective(img_src, M, (maxWidth, maxHeight))

                # Transformacja 2: Wstawienie w okno (Unwarp/Place)
                M_place = cv2.getPerspectiveTransform(dst_flat, dst_rect)
                placed_patch = cv2.warpPerspective(warped_patch, M_place, (self.target_w, self.target_h))

                # Maskowanie (tylko w obrębie kształtu szybki)
                mask = np.zeros((self.target_h, self.target_w), dtype=np.uint8)
                pts_poly = np.array(dst_pts_raw, np.int32).reshape((-1, 1, 2))
                cv2.fillPoly(mask, [pts_poly], 255)

                # Overlay
                condition = mask == 255
                canvas[condition] = placed_patch[condition]
            except Exception as e:
                print(f"[WARN] Blad mapowania strefy: {e}")
                continue

        # 3. Zapis Wyniku
        # AI_READY_flat_p3_scan_d68f...
        output_filename = f"AI_READY_{filename}"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        cv2.imwrite(output_path, canvas)
        print(f"[OK] -> {output_filename}")

        # 4. Przeniesienie oryginału do archiwum
        try:
            shutil.move(src_path, os.path.join(ARCHIVE_FOLDER, filename))
        except:
            pass

        return True

    def run_loop(self):
        print("--- WINDOW MAPPER START ---")
        print(f"Watch: {INPUT_FOLDER}")

        while True:
            try:
                # Pobierz pliki i sortuj od najstarszego
                files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
                files.sort(key=lambda x: os.path.getmtime(os.path.join(INPUT_FOLDER, x)))

                if not files:
                    time.sleep(1)
                    continue

                for filename in files:
                    print(f"Mapowanie: {filename}...", end=" ")
                    self.process_file(filename)

            except KeyboardInterrupt:
                print("\nZatrzymano.")
                break
            except Exception as e:
                print(f"[LOOP ERR] {e}")
                time.sleep(1)


if __name__ == "__main__":
    app = WindowWarper()
    app.run_loop()