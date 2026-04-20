import base64
import os

import cv2
import numpy as np
import requests
from PIL import Image
from utils.logging_config import get_stock_logger

from .preprocess import smart_slice_tall_image

logger = get_stock_logger()
API_URL = "https://r499p5s59cg8zev7.aistudio-app.com/ocr"
API_TOKEN = "3a258219dc655d3bafc108d13cb9ec6230a7ff9a"

# --- Add a global variable for lazy-loading the local model ---
_local_ocr_instance = None


def _get_local_ocr():
    """Lazily initialize the local PaddleOCR model to save memory if API is working."""
    global _local_ocr_instance
    if _local_ocr_instance is None:
        logger.info("Initializing local PaddleOCR model for fallback...")
        try:
            from paddleocr import PaddleOCR
            # lang='ch' supports both English and Chinese. Change to 'en' if English only.
            _local_ocr_instance = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            logger.info("Local PaddleOCR model initialized successfully.")
        except ImportError:
            logger.error("Failed to import PaddleOCR. Please run: pip install paddlepaddle paddleocr")
            return None
    return _local_ocr_instance


def _ocr_via_local(file_path: str):
    """Run OCR using the local PaddleOCR model and format outputs."""
    ocr = _get_local_ocr()
    if ocr is None:
        return [], []

    try:
        # result format: [[[[x,y],[x,y],[x,y],[x,y]], ('text', confidence)], ...]
        result = ocr.ocr(file_path, cls=True)

        # If no text found, paddleocr returns [None] or []
        if not result or not result[0]:
            return [], []

        boxes = []
        texts = []
        for line in result[0]:
            boxes.append(line[0])  # The 4 polygon points
            texts.append(line[1][0])  # The text string

        return texts, boxes
    except Exception as e:
        logger.error(f"Local OCR failed for {file_path}. Error: {e}")
        return [], []


def _is_tall_image(img, ratio=2.0):
    w, h = img.size
    return h / w >= ratio


def _ocr_via_api(
        file_path: str,
        file_type: int = 1,
        use_doc_orientation_classify: bool = False,
        use_doc_unwarping: bool = False,
        use_textline_orientation: bool = False,
        retry: int = 3
):
    """
    Call PaddleOCR HTTP API on a single image or PDF and return the raw result dict.
    """
    if not API_TOKEN or API_TOKEN == "<access token>":
        logger.error("API_TOKEN is not configured. Please set PADDLE_OCR_API_TOKEN env or replace '<access token>'.")
        return None

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except OSError as e:
        logger.error(f"Failed to read image for OCR: {file_path}, error: {e}")
        return None

    file_data = base64.b64encode(file_bytes).decode("ascii")

    headers = {
        "Authorization": f"token {API_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "file": file_data,
        "fileType": file_type,  # 0 for PDF, 1 for images
        "useDocOrientationClassify": use_doc_orientation_classify,
        "useDocUnwarping": use_doc_unwarping,
        "useTextlineOrientation": use_textline_orientation,
    }

    response = None
    for i in range(retry):
        try:
            response = requests.post(API_URL, json=payload, headers=headers, timeout=30)
            break
        except Exception as e:
            logger.error(f"HTTP error while calling PaddleOCR API: {e}, retrying...")

    if response is None:
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error("Failed to decode JSON from PaddleOCR API response.")
        return None

    result = data.get("result")
    if not result:
        logger.error("No 'result' field found in PaddleOCR API response.")
        return None

    return result


def _sort_ocr_results(texts, boxes, y_tolerance_ratio=0.6, x_tolerance_ratio=12):
    """
    OCR an image and return texts sorted top-to-bottom and left-to-right.
    Line grouping considers both vertical proximity and minimal horizontal edge distance.
    """

    # Compute mean height for tolerance
    heights = []
    for box in boxes:
        h1 = abs(box[0][1] - box[3][1])
        h2 = abs(box[1][1] - box[2][1])
        heights.append((h1 + h2) / 2)
    mean_height = np.mean(heights) if heights else 10
    y_tol = mean_height * y_tolerance_ratio

    # Prepare text info: (y_center, x_center, text, box)
    txy_text = []
    for i, box in enumerate(boxes):
        # Parallelogram centroid: average of all four vertices
        centroid_x = sum([p[0] for p in box]) / 4
        centroid_y = sum([p[1] for p in box]) / 4
        txy_text.append((centroid_y, centroid_x, texts[i], box))

    # Sort all by y then x
    txy_text.sort(key=lambda r: (r[0], r[1]))

    def box_x_range(box):
        """Return (min_x, max_x) of the box."""
        xs = [p[0] for p in box]
        return min(xs), max(xs)

    sorted_texts = []
    cur_row = []
    prev_y = None

    for y, x, t, box in txy_text:
        if not cur_row:
            cur_row = [(y, x, t, box)]
            prev_y = y
            continue

        mean_y = np.mean([r[0] for r in cur_row])
        y_gap = abs(y - mean_y)

        # --- compute minimal horizontal edge distance ---
        bx_min, bx_max = box_x_range(box)
        min_x_gap = float("inf")
        for _, _, _, b2 in cur_row:
            b2_min, b2_max = box_x_range(b2)
            # distance between two boxes horizontally (0 if overlapping)
            gap = max(0, max(b2_min - bx_max, bx_min - b2_max))
            if gap < min_x_gap:
                min_x_gap = gap

        # Adjust y tolerance based on x distance
        effective_y_tol = y_tol / (1 + min_x_gap / (mean_height * x_tolerance_ratio))

        if y_gap <= effective_y_tol:
            cur_row.append((y, x, t, box))
            prev_y = (prev_y + y) / 2
        else:
            cur_row.sort(key=lambda r: r[1])
            sorted_texts.extend([r[2] for r in cur_row])
            cur_row = [(y, x, t, box)]
            prev_y = y

    if cur_row:
        cur_row.sort(key=lambda r: r[1])
        sorted_texts.extend([r[2] for r in cur_row])

    return sorted_texts


def ocr_image_safe(
        img_path,
        y_tolerance_ratio=0.6,
        x_tolerance_ratio=12,
        tall_ratio=1.5
):
    img = Image.open(img_path)
    all_sorted_texts = []
    if _is_tall_image(img, tall_ratio):
        logger.info("Tall image detected, slicing before OCR")
        slices = smart_slice_tall_image(img)
    else:
        slices = [img]
    pad_ratio = 0.05

    img_dir = os.path.dirname(os.path.abspath(img_path))
    for idx, crop in enumerate(slices):
        img = crop
        w, h = img.size
        pad = int(min(w, h) * pad_ratio)

        padded_img = Image.new("L", (w + pad * 2, h + pad * 2), 255)
        padded_img.paste(img, (pad, pad))

        padded_img_save_path = os.path.join(
            img_dir,
            f"ocr_slice_{idx}_padded.png"
        )
        try:
            padded_img.save(padded_img_save_path)
            logger.info(f"Saved OCR padded image: {padded_img_save_path}")
        except Exception as e:
            logger.warning(f"Failed to save padded image: {e}")

        # ---------------------------------------------------------
        # API CALL WITH LOCAL FALLBACK LOGIC
        # ---------------------------------------------------------
        texts, boxes = [], []
        api_success = False

        # 1. Attempt API
        api_result = _ocr_via_api(
            padded_img_save_path,
            file_type=1,
            use_textline_orientation=True,
        )

        if api_result:
            ocr_results = api_result.get("ocrResults", [])
            result = [r.get("prunedResult", "") for r in ocr_results if r.get("prunedResult")]

            if result and isinstance(result, list):
                res = result[0]
                texts = res.get("rec_texts", [])
                boxes = res.get("rec_polys", [])

                if texts and boxes:
                    api_success = True
                    logger.info("Successfully extracted text via API.")

        # 2. Fallback to Local Model if API failed or returned empty
        if not api_success:
            logger.warning("API OCR failed or returned empty. Falling back to local model.")
            texts, boxes = _ocr_via_local(padded_img_save_path)

        # 3. If both failed/returned nothing, skip this slice
        if not texts or not boxes:
            logger.warning(f"No text detected by either API or Local model for {padded_img_save_path}")
            continue
        # ---------------------------------------------------------

        sorted_texts = _sort_ocr_results(
            texts,
            boxes,
            y_tolerance_ratio,
            x_tolerance_ratio
        )
        all_sorted_texts.extend(sorted_texts)

        # Draw boxes on the image
        img_cv2 = cv2.imread(padded_img_save_path)
        for box in boxes:
            pts = np.array(box, np.int32)
            pts = pts.reshape((-1, 1, 2))
            cv2.polylines(img_cv2, [pts], isClosed=True, color=(0, 0, 255), thickness=2)

        root, ext = os.path.splitext(padded_img_save_path)
        drawn_img_path = f"{root}_with_boxes{ext}"
        cv2.imwrite(drawn_img_path, img_cv2)
        logger.info(f"Saved image with drawn boxes to: {drawn_img_path}")

    return all_sorted_texts