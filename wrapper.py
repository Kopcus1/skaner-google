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

# --- NOWOŚĆ: Ścieżka do full_content ---
EXTRA_OUTPUT_ROOT = os.path.join(CURRENT_DIR, "full_content")

# ARCHIWUM: Tu trafiają skany po nałożeniu na okno
ARCHIVE_FOLDER = os.path.join(BASE_DIR, "CROPPED_ARCHIVE")

# PLIKI KONFIGURACYJNE
CONFIG_FILE = "full_map_config.json"
BACKGROUND_FILE = "window_mapper_window.png"


class WindowWarper:
    def __init__(self):
        self.ensure_dirs()
        self.load_config()
        self.check_background()

    def ensure_dirs(self):
        # Dodajemy EXTRA_OUTPUT_ROOT do listy folderów
        for path in [INPUT_FOLDER, OUTPUT_FOLDER, ARCHIVE_FOLDER, EXTRA_OUTPUT_ROOT]:
            if not os.path.exists(path):
                os.makedirs(path)
                print(f"[INIT] Utworzono folder: {path}")

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            print(f"[CRITICAL] Brak pliku {CONFIG_FILE}!")
            sys.exit(1)

        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                self.mapping = data['mapping']
                self.target_w = data.get('target_w', 1920)
                self.target_h = data.get('target_h', 1080)
            print(f"[CONFIG] Zaladowano {len(self.mapping)} stref mapowania.")
        except Exception as e:
            print(f"[ERROR] Blad odczytu configu: {e}")
            sys.exit(1)

    def check_background(self):
        if not os.path.exists(BACKGROUND_FILE):
            print(f"[WARN] Brak pliku tla {BACKGROUND_FILE}! Tlo bedzie czarne.")
        else:
            print(f"[BG] Wykryto tlo: {BACKGROUND_FILE}")

    def get_four_corners(self, pts):
        pts = np.array(pts, dtype="float32")
        if len(pts) == 4:
            return pts
        indices = np.argsort(pts[:, 1])
        top_indices = indices[:3]
        top_pts = pts[top_indices]
        top_x_indices = np.argsort(top_pts[:, 0])
        idx_to_remove = top_indices[top_x_indices[1]]
        return np.delete(pts, idx_to_remove, axis=0)

    def order_points(self, pts):
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

    def process_file(self, filename):
        src_path = os.path.join(INPUT_FOLDER, filename)
        time.sleep(0.5)

        img_src = cv2.imread(src_path)
        if img_src is None:
            print(f"[SKIP] Nie mozna otworzyc: {filename}")
            return False

        # 1. Przygotowanie warstwy RYSUNKU (To będzie "pod spodem")
        # Tworzymy puste (czarne) płótno o docelowych wymiarach
        drawing_layer = np.zeros((self.target_h, self.target_w, 3), dtype=np.uint8)

        # 2. Mapowanie (Rysujemy wykrzywione fragmenty na drawing_layer)
        for zone in self.mapping:
            try:
                src_rect = self.order_points(self.get_four_corners(zone['source_points']))
                dst_rect = self.order_points(self.get_four_corners(zone['target_points']))

                widthA = np.sqrt(((dst_rect[0][0] - dst_rect[1][0]) ** 2) + ((dst_rect[0][1] - dst_rect[1][1]) ** 2))
                widthB = np.sqrt(((dst_rect[2][0] - dst_rect[3][0]) ** 2) + ((dst_rect[2][1] - dst_rect[3][1]) ** 2))
                maxWidth = max(int(widthA), int(widthB))

                heightA = np.sqrt(((dst_rect[0][0] - dst_rect[3][0]) ** 2) + ((dst_rect[0][1] - dst_rect[3][1]) ** 2))
                heightB = np.sqrt(((dst_rect[1][0] - dst_rect[2][0]) ** 2) + ((dst_rect[1][1] - dst_rect[2][1]) ** 2))
                maxHeight = max(int(heightA), int(heightB))

                dst_flat = np.array([[0, 0], [maxWidth - 1, 0], [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]],
                                    dtype="float32")

                M = cv2.getPerspectiveTransform(src_rect, dst_flat)
                warped_patch = cv2.warpPerspective(img_src, M, (maxWidth, maxHeight))

                M_place = cv2.getPerspectiveTransform(dst_flat, dst_rect)
                placed_patch = cv2.warpPerspective(warped_patch, M_place, (self.target_w, self.target_h))

                # Tworzymy maskę, gdzie ma trafić ten kawałek rysunku
                mask = np.zeros((self.target_h, self.target_w), dtype=np.uint8)
                pts_poly = np.array(zone['target_points'], np.int32).reshape((-1, 1, 2))
                cv2.fillPoly(mask, [pts_poly], 255)

                condition = mask == 255
                # Wpisujemy rysunek na warstwę "pod spodem"
                drawing_layer[condition] = placed_patch[condition]
            except Exception as e:
                print(f"[WARN] Blad strefy: {e}")
                continue

        # 3. Ostatni szlif: Nakładanie Overlay (Ramy okiennej)
        # Wczytujemy z flagą UNCHANGED, żeby zachować kanał Alpha
        if os.path.exists(BACKGROUND_FILE):
            overlay = cv2.imread(BACKGROUND_FILE, cv2.IMREAD_UNCHANGED)

            # Skalowanie overlay, jeśli rozmiar się nie zgadza
            if overlay.shape[1] != self.target_w or overlay.shape[0] != self.target_h:
                overlay = cv2.resize(overlay, (self.target_w, self.target_h))

            # Sprawdzamy czy mamy kanał Alfa (4 kanały: BGRA)
            if overlay.shape[2] == 4:
                # Rozdzielamy kanały
                b, g, r, a = cv2.split(overlay)
                foreground = cv2.merge((b, g, r))  # To jest rama okna

                # Normalizujemy alfę do zakresu 0.0 - 1.0
                alpha = a.astype(float) / 255.0

                # Rozszerzamy alfę na 3 kanały, żeby pasowała do mnożenia macierzy RGB
                alpha = cv2.merge((alpha, alpha, alpha))

                # --- KLUCZOWA LOGIKA MIESZANIA (ALPHA BLENDING) ---
                # Wzór: Pixel = (Rama * Alpha) + (Rysunek * (1 - Alpha))
                # Tam gdzie Alpha=1 (rama), widzimy ramę.
                # Tam gdzie Alpha=0 (szyba), widzimy rysunek spod spodu.

                final_image_float = (foreground.astype(float) * alpha) + (drawing_layer.astype(float) * (1.0 - alpha))
                canvas = final_image_float.astype(np.uint8)
            else:
                # Jeśli plik tła nie ma przezroczystości, po prostu go nakładamy (zasłoni rysunek!)
                # Ewentualnie można tu dać fallback. Zakładam, że plik jest poprawny.
                print("[WARN] Tło nie ma kanału Alfa! Nadpisuję rysunek.")
                canvas = overlay[:, :, :3]
        else:
            # Brak pliku tła - zostaje sam rysunek na czarnym tle
            canvas = drawing_layer

        # 4. Zapis Wyniku A (AI INPUT)
        output_filename = f"{filename}"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        cv2.imwrite(output_path, canvas)

        # 5. Zapis Wyniku B (FULL CONTENT -> wrap.jpg)
        try:
            folder_name_only = os.path.splitext(filename)[0]
            file_extension = os.path.splitext(filename)[1]
            fixed_name = f"wrap{file_extension}"

            extra_dir_path = os.path.join(EXTRA_OUTPUT_ROOT, folder_name_only)
            extra_file_path = os.path.join(extra_dir_path, fixed_name)

            os.makedirs(extra_dir_path, exist_ok=True)

            cv2.imwrite(extra_file_path, canvas)
            print(f"[OK] Saved: AI_INPUT & FULL/{folder_name_only}/{fixed_name}")

        except Exception as e:
            print(f"[WARN] Nie udalo sie zapisac kopii wrap: {e}")

        # 6. Archiwizacja
        try:
            shutil.move(src_path, os.path.join(ARCHIVE_FOLDER, filename))
        except:
            pass

        return True

    def run_loop(self):
        print("--- WINDOW MAPPER START ---")
        print(f"Watch: {INPUT_FOLDER}")
        print(f"Extra: {EXTRA_OUTPUT_ROOT} (wrap.jpg)")

        while True:
            try:
                files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
                files.sort(key=lambda x: os.path.getmtime(os.path.join(INPUT_FOLDER, x)))

                if not files:
                    time.sleep(1)
                    continue

                for filename in files:
                    print(f"Mapowanie: {filename}...", end=" ")
                    self.process_file(filename)

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[LOOP ERR] {e}")
                time.sleep(1)


if __name__ == "__main__":
    app = WindowWarper()
    app.run_loop()