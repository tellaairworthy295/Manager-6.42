
import re
import unicodedata
import cv2
import numpy as np
from PIL import Image
import os
import matplotlib.pyplot as plt
from paddleocr import PaddleOCR
import pandas as pd
from matplotlib import font_manager
from config import setup_logging

logger = setup_logging("logs/jiuyan", "jiuyan_scraper")
FONT_PATH = "fonts/NotoSansSC-VariableFont_wght.ttf"

def _draw_boxes(img_path):
    """
    OCR an image, draw all OCRed text boxes on the image and save the image with drawn boxes.
    """
    ocr = PaddleOCR(lang='ch')
    result = ocr.predict(img_path)
    if not result or not isinstance(result, list):
        return [], []

    res = result[0]
    texts = res["rec_texts"]
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
    boxes = res["rec_polys"]

    # Load the image using cv2
    img = cv2.imread(img_path)
    if img is None:
        logger.error(f"Failed to load image: {img_path}")
        return [], []

    # Draw each box (polygon) on the image
    for box in boxes:
        pts = np.array(box, np.int32)
        pts = pts.reshape((-1, 1, 2))
        cv2.polylines(img, [pts], isClosed=True, color=(0, 0, 255), thickness=2)

    # Save the drawn image
    root, ext = os.path.splitext(img_path)
    drawn_img_path = f"{root}_with_boxes{ext}"
    cv2.imwrite(drawn_img_path, img)
    logger.info(f"✅ Saved image with drawn boxes to: {drawn_img_path}")

    return boxes, drawn_img_path
    
def _normalize_text(text: list[str], space: bool = False) -> str:
        """Clean and normalize a label string (Chinese/English mixed)."""
        for idx, label in enumerate(text):
            if not label:
                continue
            label = unicodedata.normalize('NFKC', label)
            label = (label.replace("–", "-")
                        .replace("－", "-")
                        .replace("—", "-")
                        .replace("／", "/")
                        .replace("\\", "/")
                        .replace("：", ":"))
            if not space:
                label = re.sub(r"[\s\u3000\u00A0]+", "", label)
            label = label.replace("（", "(").replace("）", ")")
            text[idx] = label.strip()
    
def _preprecess_image(img_path):
    img = Image.open(img_path)
    w, h = img.size
    pad = int(min(w, h) * 0.05)
    padded_img = Image.new("RGB", (w + pad * 2, h + pad * 2), (255, 255, 255))
    padded_img.paste(img, (pad, pad))
    padded_img_array = cv2.cvtColor(np.array(padded_img), cv2.COLOR_RGB2BGR)

    gray = cv2.cvtColor(padded_img_array, cv2.COLOR_BGR2GRAY)

    # 3️⃣ threshold (keeps text, removes light watermark)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # Apply histogram equalization after thresholding for further enhancement
    hist = cv2.equalizeHist(thresh)
    
    root, ext = os.path.splitext(img_path)
    new_img_path = f"{root}_grayclean{ext}"
    cv2.imwrite(new_img_path, hist)

    logger.info(f"✅ Saved grayscale + enhanced-contrast + thresholded to: {new_img_path}")
    return new_img_path

def _ocr_image(img_path, y_tolerance_ratio=0.6, x_tolerance_ratio=12):
    """
    OCR an image and return texts sorted top-to-bottom and left-to-right.
    Line grouping considers both vertical proximity and minimal horizontal edge distance.
    """

    ocr = PaddleOCR(lang='ch', use_textline_orientation=True)
    result = ocr.predict(img_path)
    if not result or not isinstance(result, list):
        return []

    res = result[0]
    texts = res.get("rec_texts", [])
    boxes = res.get("rec_polys", [])
    if not texts or not boxes or len(texts) != len(boxes):
        return texts

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


def _append_to_excel(date_str: str, rec_texts, excel_path: str):
    """
    Save OCR-recognized texts to an Excel file grouped by 8 fields:
    ["Section", "Board", "Code", "Name", "Time", "Market", "Turnover", "Keyword"].
    Automatically creates directories if needed.
    """
    # Ensure the output directory exists
    os.makedirs(excel_path, exist_ok=True)

    # Expected columns (in order)
    cols = ["Section", "Board", "Code", "Name", "Time", "Market", "Turnover", "Keyword"]

    # Clean up text list (remove empty strings, spaces)
    rec_texts = [t.strip() for t in rec_texts if t.strip()]
    
    # Split into rows of 8 items
    data = []
    i = 0
    while i < len(rec_texts):
        m = re.search(r"^(.+)\*(\d+)$", rec_texts[i])
        if not m and i + 1 < len(rec_texts) and "亿元" not in rec_texts[i] and "涨停关键词" not in rec_texts[i]:
            m = re.search(r"^(.+)\*(\d+)$", rec_texts[i] + rec_texts[i+1])
            i = i + 1
        if m:
            section_name = m.group(1)
            row_num = int(m.group(2))
            i += 1  # move to first row under this section

            for _ in range(row_num):
                # Get 7 items (since Section will be added manually)
                if i + 7 <= len(rec_texts):
                    chunk = rec_texts[i:i+7]
                    if chunk[0] == "7":
                        chunk[0] = "1"
                    if chunk[-1] == "1":
                        chunk = [chunk[-1]] + chunk[:-1]
                    data.append([section_name] + chunk)
                    i += 7
                else:
                    break
        else:
            i += 1

    if not data:
        logger.error("⚠️ No valid data to save.")
        return

    # Convert to DataFrame
    df = pd.DataFrame(data, columns=cols)

    # Output path for this date
    save_path = os.path.join(excel_path, f"{date_str}.xlsx")

    # Save to Excel
    df.to_excel(save_path, index=False)
    logger.info(f"✅ Saved {len(df)} rows to {save_path}")


def _process_trendings(date_str: str, summary_text: str, excel_path: str, days: int):
    """
    Extract trending stats from summary_text, update Excel file, and generate chart.
    Uses openpyxl engine to avoid xlrd errors.
    """
    trendings = {
        "limit_up":  r"涨停(\d+)家",
        "limit_down": r"跌停(\d+)家",
        "even_board": r"连板(\d+)家",
        "breaking_rate": r"破板率(\d+\.?\d+?)%"
    }

    # 1️⃣ Extract values with regex
    results = {}
    for key, pattern in trendings.items():
        match = re.search(pattern, summary_text)
        try:
            results[key] = int(match.group(1)) if match else None
        except:
            results[key] = float(match.group(1)) if match else None

    new_row = {
        "date": date_str,
        "up": results["limit_up"],
        "down": results["limit_down"],
        "even": results["even_board"],
        "break_rate": results["breaking_rate"]
    }

    # 3️⃣ If Excel exists, read only the last `days` rows; otherwise create new DataFrame
    if os.path.exists(excel_path):
        try:
            df = pd.read_excel(
                excel_path,
                usecols=["date", "up", "down", "even", "break_rate"],
                engine="openpyxl"
            )
            df.columns = ["date", "up", "down", "even", "break_rate"]
        except ValueError:
            df = pd.DataFrame(columns=["date", "up", "down", "even", "break_rate"])
    else:
        df = pd.DataFrame(columns=["date", "up", "down", "even", "break_rate"])

    # Update or append the new_row for current date_str
    # Remove any existing entry for this date_str first
    df = df[df["date"].astype(str) != str(date_str)]
    # Append the new_row for current date_str
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df = df.sort_values(by="date").reset_index(drop=True)
    df.to_excel(excel_path, index=False, engine="openpyxl")
    # Always keep last `days` rows INCLUDING current date_str data
    if len(df) > days:
        df = df.tail(days).reset_index(drop=True)

    # 📈 Draw line charts for up, down, even, break_rate, and set font to support CJK to eliminate glyph warnings

    prop = font_manager.FontProperties(fname=FONT_PATH)
    df_plot = df.tail(days).copy()
    df_plot["date"] = pd.to_datetime(df_plot["date"]).dt.strftime("%m-%d")

    chart_infos = [
        ("up",      "tab:blue",   "涨停数 (Up)"),
        ("down",    "tab:red",    "跌停数 (Down)"),
        ("even",    "tab:green",  "连板数 (Even Board)"),
        ("break_rate", "tab:purple", "破板率 (Break Rate %)"),
    ]
    chart_files = [
        "_trend_up.png", "_trend_down.png", "_trend_even.png", "_trend_break.png"
    ]
    ylabel = ["Count", "Count", "Count", "Percent"]

    for i, (col, color, title) in enumerate(chart_infos):
        plt.figure(figsize=(7,3))
        plt.plot(df_plot["date"], df_plot[col], marker='o', color=color)
        plt.title(f"{title} - Recent {days} Days", fontproperties=prop)
        plt.xlabel("Date", fontproperties=prop)
        plt.ylabel(ylabel[i], fontproperties=prop)
        plt.grid(True)
        plt.tight_layout()
        chart_path = os.path.splitext(excel_path)[0] + chart_files[i]
        plt.savefig(chart_path, dpi=160)
        plt.close()
        logger.info(f"📈 Saved trend chart: {chart_path}")

def excel_flow(date: str, days: int = 5):
    img_path = f"scraped_images/{date}.png"
    if not os.path.isfile(img_path):
        logger.error(f"Image file does not exist: {img_path}")
        return None
    path = _preprecess_image(img_path)
    #_draw_boxes(path)
    rec_texts = _ocr_image(path)
    #print(rec_texts)
    _normalize_text(rec_texts)
    _append_to_excel(date, rec_texts, "excel")
    for token in rec_texts:
        if "涨停" in token and "未开板新股" in token:
            _process_trendings(date, token, "excel/trendings.xlsx", days)
            break
    try:
        os.remove(path)
    except:
        pass

if __name__ == "__main__":
    excel_flow("2025-12-03")