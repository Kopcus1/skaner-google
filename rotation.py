import cv2
import numpy as np
import os
import time
import shutil
import math
import sys

# --- KONFIGURACJA ŚCIEŻEK ---
CURRENT_DIR = os.getcwd()
BASE_DIR = os.path.join(CURRENT_DIR, "content")

# Folder ze zdjęciami od Skanera
INPUT_DIR = os.path.join(BASE_DIR, "RAW_PHOTO")
# Folder wynikowy (Wyprostowane + Resize)
OUTPUT_DIR = os.path.join(BASE_DIR, "CROPPED")

# Folder zbiorczy (struktura katalogów)
EXTRA_OUTPUT_ROOT = os.path.join(CURRENT_DIR, "full_content")

# Archiwum i Błędy
ARCHIVE_DIR = os.path.join(BASE_DIR, "ARCHIVE_RAW")
ERROR_DIR = os.path.join(BASE_DIR, "ERROR_RAW")

VALID_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp')

# --- DOCELOWE WYMIARY ---
FINAL_WIDTH = 486
FINAL_HEIGHT = 727


def setup_directories():
    for path in [INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR, EXTRA_OUTPUT_ROOT]:
        if not os.path.exists(path):
            os.makedirs(path)


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


def robust_qr_detect(img_path):
    original_img = cv2.imread(img_path)
    if original_img is None: return None, None, None, None

    height, width = original_img.shape[:2]
    target_width = 1600
    scale_factor = 1.0
    if width > target_width:
        scale_factor = target_width / width

    if scale_factor < 1.0:
        small_img = cv2.resize(original_img, (0, 0), fx=scale_factor, fy=scale_factor)
    else:
        small_img = original_img.copy()

    detector = cv2.QRCodeDetector()
    retval, decoded, points, _ = detector.detectAndDecodeMulti(small_img)

    if not retval:
        gray = cv2.cvtColor(small_img, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
        retval, decoded, points, _ = detector.detectAndDecodeMulti(binary)

    if retval and decoded:
        valid_count = sum(1 for info in decoded if info and "_" in info)
        if valid_count >= 3:
            real_points = points / scale_factor
            return True, decoded, real_points, original_img

    return False, None, None, original_img


def process_image(filename):
    file_path = os.path.join(INPUT_DIR, filename)

    retval, decoded_info, points, img = robust_qr_detect(file_path)

    if not retval: return False, "Brak kodow QR"

    markers = {}
    detected_ids = []

    for i, info in enumerate(decoded_info):
        if not info: continue
        if "_" in info:
            parts = info.split('_')
            pos = parts[0]
            if len(parts) > 1:
                detected_ids.append(parts[1])

            center_x = np.mean(points[i][:, 0])
            center_y = np.mean(points[i][:, 1])
            markers[pos] = (center_x, center_y)

    final_pattern_id = "unknown"
    if detected_ids:
        final_pattern_id = max(set(detected_ids), key=detected_ids.count)

    required = ['TL', 'TR', 'BR', 'BL']
    found_count = len([k for k in required if k in markers])
    reconstructed_key = None

    if found_count == 3:
        markers, reconstructed_key = estimate_missing_point(markers)
    elif found_count < 3:
        return False, f"Za malo punktow: {found_count}/4"

    try:
        src_pts = np.array([markers['TL'], markers['TR'], markers['BR'], markers['BL']], dtype="float32")

        width_a = distance(markers['TL'], markers['TR'])
        width_b = distance(markers['BL'], markers['BR'])
        max_width = max(int(width_a), int(width_b))

        height_a = distance(markers['TL'], markers['BL'])
        height_b = distance(markers['TR'], markers['BR'])
        max_height = max(int(height_a), int(height_b))

        dst_pts = np.array([
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1]
        ], dtype="float32")

        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        warped_img = cv2.warpPerspective(img, M, (max_width, max_height))

        # 1. Rotacja i Resize
        warped_img = cv2.rotate(warped_img, cv2.ROTATE_90_CLOCKWISE)
        warped_img = cv2.resize(warped_img, (FINAL_WIDTH, FINAL_HEIGHT), interpolation=cv2.INTER_AREA)

        # --- ZAPIS A: Folder CROPPED (Nazwa oryginalna) ---
        output_filename = filename
        output_path_cropped = os.path.join(OUTPUT_DIR, output_filename)
        cv2.imwrite(output_path_cropped, warped_img)

        # --- ZAPIS B: Folder FULL_CONTENT (Nazwa "scan.jpg") ---
        folder_name_only = os.path.splitext(filename)[0]  # Folder = nazwa pliku bez .jpg
        file_extension = os.path.splitext(filename)[1]  # np. .jpg

        # Nazwa w podfolderze ZAWSZE "scan.jpg"
        fixed_name = f"scan{file_extension}"

        extra_dir_path = os.path.join(EXTRA_OUTPUT_ROOT, folder_name_only)
        extra_file_path = os.path.join(extra_dir_path, fixed_name)

        os.makedirs(extra_dir_path, exist_ok=True)
        cv2.imwrite(extra_file_path, warped_img)

        info_extra = f"ID:{final_pattern_id} -> Saved: CROPPED/{output_filename} & FULL/{folder_name_only}/{fixed_name}"
        if reconstructed_key: info_extra += f", Reco:{reconstructed_key}"

        return True, f"OK [{info_extra}]"

    except Exception as e:
        return False, f"Geometria blad: {e}"


def main_loop():
    setup_directories()
    print("--- PROCESSOR DEWARP START ---")
    print(f"Watch: {INPUT_DIR}")
    print(f"Target 1: {OUTPUT_DIR} (Original Name)")
    print(f"Target 2: {EXTRA_OUTPUT_ROOT} (Name: scan.jpg)")

    while True:
        try:
            files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(VALID_EXTENSIONS)]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(INPUT_DIR, x)))

            if not files:
                time.sleep(0.5)
                continue

            for filename in files:
                input_path = os.path.join(INPUT_DIR, filename)
                time.sleep(0.5)

                print(f"Przetwarzam: {filename}...", end=" ")

                try:
                    success, msg = process_image(filename)
                    if success:
                        print(f"{msg}")
                        shutil.move(input_path, os.path.join(ARCHIVE_DIR, filename))
                    else:
                        print(f"SKIP ({msg})")
                        shutil.move(input_path, os.path.join(ERROR_DIR, filename))
                except Exception as e:
                    print(f"CRASH: {e}")
                    try:
                        shutil.move(input_path, os.path.join(ERROR_DIR, f"CRASH_{filename}"))
                    except:
                        pass

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error loop: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main_loop()