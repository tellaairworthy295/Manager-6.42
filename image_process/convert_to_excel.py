import os
import re
import unicodedata
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager
from .preprocess import preprecess_image
from .ocr_api import ocr_image_safe
import matplotlib
matplotlib.use('Agg')
# Configure loguru for to_excel module
from utils.logging_config import get_stock_logger

# 2️⃣ Add file logger (safe, no buffering issues)
logger = get_stock_logger()

FONT_PATH = "fonts/NotoSansSC-VariableFont_wght.ttf"

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


def _append_to_excel(rec_texts: list[str], excel_path: str, date:str):
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
                    m_0 = re.search(r"^(\d{6})", chunk[0].strip())
                    if m_0:
                        chunk = ['1'] + chunk[:-1]
                        skip = 6
                    else:
                        if chunk[0] == "7":
                            chunk[0] = "1"
                        if chunk[-1] == "1":
                            chunk = [chunk[-1]] + chunk[:-1]
                        skip = 7
                    data.append([section_name] + chunk)
                    i += skip
                else:
                    break
        else:
            i += 1

    if not data:
        logger.error("No valid data to save.")
        return

    # Convert to DataFrame
    df = pd.DataFrame(data, columns=cols)

    # Output path for this date
    save_path = os.path.join(excel_path, f"{date}.xlsx")

    # Save to Excel
    df.to_excel(save_path, index=False)
    logger.info(f"Saved {len(df)} rows to {save_path}")


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
    if df.empty:
        df = pd.DataFrame([new_row])
    else:
        df.loc[len(df)] = new_row
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
        logger.info(f"Saved trend chart: {chart_path}")


def excel_flow(date: str, days: int = 5):
    scraped_dir = "images"
    img_path = f"{scraped_dir}/Image.png"
    if not os.path.isfile(img_path):
        logger.error(f"Image file does not exist: {img_path}")
        return None
    try:
        path = preprecess_image(img_path)
        rec_texts = ocr_image_safe(path)
        _normalize_text(rec_texts)
        _append_to_excel(rec_texts, "excel", date)
        for token in rec_texts:
            if "涨停" in token and "跌停" in token:
                _process_trendings(date, token, "excel/trendings.xlsx", days)
                break
    except Exception as e:
        logger.error(f"Error while OCR: {e}")

if __name__ == "__main__":
    excel_flow("2025-12-25")
