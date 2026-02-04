import os
import cv2
import numpy as np
from PIL import Image
from utils.logging_config import get_stock_logger

logger = get_stock_logger()
def horizontal_projection(bin_img):
    """
    bin_img: binary image (text=0, background=255)
    returns array of ink density per row
    """
    return np.sum(bin_img == 0, axis=1)

def find_safe_cut_lines(
    bin_img,
    min_gap_height=20,
    max_ink_ratio=0.001
):
    """
    Returns y positions suitable for slicing
    """
    h, w = bin_img.shape
    projection = horizontal_projection(bin_img)

    max_ink = w * max_ink_ratio
    safe_rows = projection < max_ink

    cut_lines = []
    start = None

    for y, is_safe in enumerate(safe_rows):
        if is_safe and start is None:
            start = y
        elif not is_safe and start is not None:
            if y - start >= min_gap_height:
                cut_lines.append((start + y) // 2)
            start = None

    return cut_lines


def choose_cut_near(cut_lines, target_y, max_shift=200):
    candidates = [y for y in cut_lines if abs(y - target_y) <= max_shift]
    if not candidates:
        return target_y  # fallback
    return min(candidates, key=lambda y: abs(y - target_y))


def smart_slice_tall_image(
    img,
    target_height=2000,
    overlap=0
):
    gray = np.array(img)
    h, w = gray.shape
    cut_lines = find_safe_cut_lines(gray)

    slices = []
    y = 0

    while y < h:
        target_y = min(y + target_height, h)
        cut_y = choose_cut_near(cut_lines, target_y)

        crop = img.crop((0, y, w, cut_y))
        slices.append(crop)

        if cut_y == h:
            break

        y = max(cut_y - overlap, y + 1)

    return slices


def _is_tall_image(img, ratio=2.0):
    w, h = img.size
    return h / w >= ratio


def preprocess_image(img_path):
    img = Image.open(img_path)
    img_array = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_array, cv2.COLOR_BGR2GRAY)

    # 3️⃣ threshold (keeps text, removes light watermark)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Apply histogram equalization after thresholding for further enhancement
    hist = cv2.equalizeHist(thresh)

    root, ext = os.path.splitext(img_path)
    new_img_path = f"{root}_grayclean{ext}"
    cv2.imwrite(new_img_path, hist)

    logger.info(f"✅ Saved grayscale + enhanced-contrast + thresholded to: {new_img_path}")
    return new_img_path


