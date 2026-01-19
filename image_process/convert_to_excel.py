import os
import re
import unicodedata
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager

from utils.database import StockStatsRepository, get_db_manager
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


def _get_maxEven_maxBreak(rec_texts: list[str]):
    """
    统计连板最高板 (maxEven)，即 N天M板 且 N==M 时 M最大;
    统计断板最高板 (maxBreak)，即 N天M板 且 N>M 时 M最大。
    """
    import re

    even_list = []
    break_list = []
    pattern = re.compile(r'(\d+)天(\d+)板')

    for text in rec_texts:
        match = pattern.match(text.strip())
        if match:
            n = int(match.group(1))
            m = int(match.group(2))
            if n == m:
                even_list.append(m)
            elif n > m:
                break_list.append(m)
    maxEven = max(even_list) if even_list else 0
    maxBreak = max(break_list) if break_list else 0
    return maxEven, maxBreak

def _get_trendings(rec_texts: list[str]):
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
    for text in rec_texts:
        if "涨停" in text and "跌停" in text:
            for key, pattern in trendings.items():
                match = re.search(pattern, text)
                try:
                    results[key] = int(match.group(1)) if match else None
                except:
                    results[key] = float(match.group(1)) if match else None
            break

    return {
        "up": results["limit_up"],
        "down": results["limit_down"],
        "even": results["even_board"],
        "break_rate": results["breaking_rate"]
    }

def draw_and_save(rec_texts: list[str], market_number: dict, date: str, days: int = 90):
    """
    1. 从数据库中读取最近90天的数据，画出曲线图：
        - 破板率单独画在一张图上；
        - 涨停数、跌停数、连板数、破板数共用一个y轴（左），上涨家数/下跌家数共用一个y轴（右）
    2. 新数据写入数据库表 stock_stats
    """
    # === 字体管理 ===
    try:
        my_font = font_manager.FontProperties(fname=FONT_PATH)
    except Exception as e:
        logger.warning(f"Failed to load FONT_PATH '{FONT_PATH}': {e}, using default font.")
        my_font = None

    # 数据抽取
    max_even, max_break = _get_maxEven_maxBreak(rec_texts)
    trendings = _get_trendings(rec_texts)

    # ===保存新数据到数据库 ===
    db_manager = get_db_manager()
    repo = StockStatsRepository(db_manager)

    new_stats = {
        "date": date,
        "up": trendings["up"],
        "down": trendings["down"],
        "even": trendings["even"],
        "break_rate": trendings["break_rate"],
        "up_number": int(market_number.get("up") or 0),
        "down_number": int(market_number.get("down") or 0),
        "max_even": int(max_even),
        "max_break": int(max_break),
    }
    repo.add_market_stats(new_stats)

    # === [1] 查询最近90天数据绘图 ===
    df_records = repo.get_market_stats(days)
    if not df_records:
        logger.error("No stock_stats historical data found, not drawing charts.")
        return

    # 转为DataFrame
    df = pd.DataFrame(df_records)
    df = df.sort_values("date")  # 保证升序，日期从旧到新

    # ---- 画破板率 ----
    fig1, ax1 = plt.subplots(figsize=(8, 4))
    ax1.plot(df["date"], df["break_rate"], marker='o', color='orange', label="破板率(%)")
    ax1.set_ylabel("破板率(%)", fontproperties=my_font)
    ax1.set_xlabel("日期", fontproperties=my_font)
    ax1.set_title("破板率趋势", fontproperties=my_font)
    ax1.legend(loc="upper left", prop=my_font)

    # 设置X轴刻度
    xticks_idx = []
    n = len(df["date"])
    max_show = 7 if n > 7 else n
    if n > max_show:
        xticks_idx = [i for i in range(0, n, max(n // (max_show-1), 1))]
        # always ensure last label shows
        if xticks_idx[-1] != n-1:
            xticks_idx.append(n-1)
    else:
        xticks_idx = list(range(n))
    ax1.set_xticks([df.index[i] for i in xticks_idx])
    ax1.set_xticklabels([df["date"].iloc[i] for i in xticks_idx], rotation=90, fontproperties=my_font if my_font else None)

    for label in ax1.get_xticklabels():
        if my_font:
            label.set_fontproperties(my_font)
    for label in ax1.get_yticklabels():
        if my_font:
            label.set_fontproperties(my_font)
    plt.tight_layout()
    plt.savefig("excel/破板率趋势.png")
    plt.close(fig1)

    # ---- 多数据共图 ----
    fig2, ax_left = plt.subplots(figsize=(12, 5))
    # 左轴
    ax_left.plot(df["date"], df["up"], label="涨停家数", marker='o')
    ax_left.plot(df["date"], df["down"], label="跌停家数", marker='o')
    ax_left.plot(df["date"], df["even"], label="连板家数", marker='o')
    ax_left.plot(df["date"], df["max_even"], label="最高连板", linestyle="--", marker='x')
    ax_left.plot(df["date"], df["max_break"], label="最高断板", linestyle="--", marker='x')
    ax_left.set_ylabel("个数", fontproperties=my_font)
    ax_left.set_xlabel("日期", fontproperties=my_font)

    # 右轴
    ax_right = ax_left.twinx()
    ax_right.plot(df["date"], df["up_number"], label="上涨家数", color="#1e90ff", alpha=0.5, marker='s')
    ax_right.plot(df["date"], df["down_number"], label="下跌家数", color="#dc143c", alpha=0.5, marker='s')
    ax_right.set_ylabel("上涨/下跌家数", fontproperties=my_font)

    # 设置X轴刻度（双轴保证一致）
    n = len(df["date"])
    max_show = 7 if n > 7 else n
    if n > max_show:
        xticks_idx = [i for i in range(0, n, max(n // (max_show-1), 1))]
        if xticks_idx[-1] != n-1:
            xticks_idx.append(n-1)
    else:
        xticks_idx = list(range(n))
    ax_left.set_xticks([df.index[i] for i in xticks_idx])
    ax_left.set_xticklabels([df["date"].iloc[i] for i in xticks_idx], rotation=90, fontproperties=my_font if my_font else None)

    # X/Y轴刻度字体
    for label in ax_left.get_xticklabels() + ax_left.get_yticklabels():
        if my_font:
            label.set_fontproperties(my_font)
    for label in ax_right.get_yticklabels():
        if my_font:
            label.set_fontproperties(my_font)

    # 图例合并（左右y轴）
    handles_left, labels_left = ax_left.get_legend_handles_labels()
    handles_right, labels_right = ax_right.get_legend_handles_labels()
    ax_left.legend(handles_left + handles_right, labels_left + labels_right, loc="best", prop=my_font)
    ax_left.set_title("市场关键统计走势(近90天)", fontproperties=my_font)
    plt.tight_layout()
    plt.savefig("excel/市场统计趋势.png")
    plt.close(fig2)

    logger.info("Charts saved to excel/破板率趋势.png and excel/市场统计趋势.png")

def excel_flow(market_number: dict, date: str, days: int = 5):
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
        draw_and_save(rec_texts, market_number, date, days)
    except Exception as e:
        logger.error(f"Error while OCR: {e}")
        raise RuntimeError(f"Error while OCR: {e}")

