import base64
import os

import numpy as np
import requests
from PIL import Image
from loguru import logger

from .preprocess import smart_slice_tall_image, _is_tall_image

API_URL = "https://r499p5s59cg8zev7.aistudio-app.com/ocr"
API_TOKEN = "3a258219dc655d3bafc108d13cb9ec6230a7ff9a"


def _ocr_via_api(
    file_path: str,
    file_type: int = 1,
    use_doc_orientation_classify: bool = False,
    use_doc_unwarping: bool = False,
    use_textline_orientation: bool = False,
):
    """
    Call PaddleOCR HTTP API on a single image or PDF and return the raw result dict.
    """
    if not API_TOKEN or API_TOKEN == "<access token>":
        logger.error("🚫 API_TOKEN is not configured. Please set PADDLE_OCR_API_TOKEN env or replace '<access token>'.")
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

    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=30)
    except Exception as e:
        logger.error(f"HTTP error while calling PaddleOCR API: {e}")
        return None

    if response.status_code != 200:
        logger.error(f"PaddleOCR API returned status {response.status_code}: {response.text}")
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


def _draw_boxes(img_path):
    """
    OCR an image via HTTP API, save recognized texts to a txt file.

    The original implementation also drew polygons on the image using local
    PaddleOCR boxes. The HTTP API example only exposes text and cropped
    images, so here we focus on text extraction.
    """
    api_result = _ocr_via_api(img_path, file_type=1)
    if not api_result:
        return [], []

    ocr_results = api_result.get("ocrResults", [])
    texts = [r.get("prunedResult", "") for r in ocr_results if r.get("prunedResult")]

    # Write texts into a txt file
    txt_output_path = os.path.splitext(img_path)[0] + "_ocr_texts.txt"
    with open(txt_output_path, "w", encoding="utf-8") as f:
        for line in texts:
            if isinstance(line, str):
                f.write(line + "\n")
            elif isinstance(line, list):
                f.write(" ".join(map(str, line)) + "\n")
            else:
                f.write(str(line) + "\n")
    logger.info(f"✅ Saved OCR texts to: {txt_output_path}")

    # Bounding boxes are not handled here because the public HTTP example
    # does not document polygon data; keep API usage focused on text.
    return [], None


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
    tall_ratio=2.0
):
    img = Image.open(img_path)
    all_sorted_texts = []
    if _is_tall_image(img, tall_ratio):
        logger.info("📐 Tall image detected, slicing before OCR")
        slices = smart_slice_tall_image(img)
    else:
        slices = [img]

    img_dir = os.path.dirname(os.path.abspath(img_path))
    for idx, crop in enumerate(slices):
        # Accept PIL.Image input directly to OCR without saving temp files
        img = crop
        w, h = img.size
        pad = int(min(w, h) * 0.05)
        # Handle padding for grayscale image
        padded_img = Image.new("L", (w + pad * 2, h + pad * 2), 255)
        padded_img.paste(img, (pad, pad))
        # Save the padded image for debugging/inspection
        padded_img_save_path = os.path.join(
            img_dir,
            f"ocr_slice_{idx}_padded.png"
        )
        try:
            padded_img.save(padded_img_save_path)
            logger.info(f"💾 Saved OCR padded image: {padded_img_save_path}")
        except Exception as e:
            logger.warning(f"Failed to save padded image: {e}")

        api_result = _ocr_via_api(
            padded_img_save_path,
            file_type=1,
            use_textline_orientation=True,
        )
        if not api_result:
            continue

        ocr_results = api_result.get("ocrResults", [])
        # The HTTP example documents `prunedResult` as the text field.
        result = [r.get("prunedResult", "") for r in ocr_results if r.get("prunedResult")]

        if not result or not isinstance(result, list):
            continue

        res = result[0]
        texts = res.get("rec_texts", [])
        boxes = res.get("rec_polys", [])

        if not texts or not boxes:
            continue

        sorted_texts = _sort_ocr_results(
            texts,
            boxes,
            y_tolerance_ratio,
            x_tolerance_ratio
        )
        all_sorted_texts.extend(sorted_texts)

    return all_sorted_texts


