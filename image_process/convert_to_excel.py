import collections
import os
import re
from datetime import datetime

import unicodedata
import pandas as pd
from utils.database import StockStatsRepository, get_db_manager, ActionLimitDataRepository
from .preprocess import preprocess_image
from .ocr_api import ocr_image_safe
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


def _save_actionData(rec_texts: list[str], excel_path: str, date: str, records_list: list[dict]):
    """
    Save OCR-recognized texts to an Excel file grouped by 8 fields:
    ["Section", "Board", "Code", "Name", "Time", "Market", "Turnover", "Keyword"].
    Automatically creates directories if needed; upserts to DB.
    """
    # Ensure directory exists
    os.makedirs(excel_path, exist_ok=True)

    # Expected columns
    cols = ["Section", "Board", "Code", "Name", "Time", "Market", "Turnover", "Keyword", "Highlight"]

    # Clean text list (remove empty/blank items)
    rec_texts = [t.strip() for t in rec_texts if t and t.strip()]

    # Parse rows of 8: custom logic for Section*RowNum headers
    data = []
    i = 0
    while i < len(rec_texts):
        m = re.search(r"^(.+)\*(\d+)$", rec_texts[i])
        if not m and i + 1 < len(rec_texts) and "亿元" not in rec_texts[i] and "涨停关键词" not in rec_texts[i]:
            m = re.search(r"^(.+)\*(\d+)$", rec_texts[i] + rec_texts[i + 1])
            i += 1
        if m:
            section_name = m.group(1)
            row_num = int(m.group(2))
            i += 1  # moved past section header
            for num_row in range(row_num):
                # Attempt to get next 7
                if i + 7 <= len(rec_texts):
                    skip = 7
                    chunk = rec_texts[i:i + 7]
                    m_first_code = re.match(r"^(\d{6})", chunk[0].strip())
                    m_first_board = re.match(r"^(\d+天\d+板)$", chunk[0].strip())
                    m_last_board = re.match(r"^(1|\d+天\d+板)$", chunk[-1].strip())
                    m_last_section = re.match(r"^(.+)\*(\d+)$", chunk[-1].strip())
                    if m_last_board or m_last_section:
                        chunk = chunk[:-1] + [""]
                        skip -= 1
                    if m_first_code:
                        chunk = ['1'] + chunk[1:]
                        skip -= 1

                    if chunk[0] == "7":
                        chunk[0] = "1"
                    if num_row < 2 and m_first_board:
                        chunk = chunk + [True]
                    else:
                        chunk = chunk + [False]
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

    # Save to Excel file (one file, named by date if you want, or on given path)
    excel_file = os.path.join(excel_path, f"actionData_{date}.xlsx")
    df.to_excel(excel_file, index=False)
    logger.info(f"Saved action data to Excel: {excel_file}")

    db_manager = get_db_manager()
    repo = ActionLimitDataRepository(db_manager)
    data_dict_list = []

    # Build a mapping from Name to df row (for matching by stock name)
    name_to_dfrow = collections.OrderedDict()
    for idx, row in df.iterrows():
        stock_name = row.get("Name")
        if stock_name:  # it should never be empty
            name_to_dfrow[stock_name] = row

    # Now, for each record in records_list, match with df using the stock/name key
    for record in records_list:
        try:
            rec_name = record.get("stock", "")
            df_row = name_to_dfrow.get(rec_name)
            if df_row is None:
                logger.warning(f"No matching row in OCR DataFrame for stock: '{rec_name}'")
                continue

            # Compose analysis and keywords if available from df and records_list
            analysis = record.get("analysis", "")
            keyword = df_row.get("Keyword", "") if "Keyword" in df_row else ""
            combined_keyword = (analysis + "\n韭研:\n" + keyword).strip() if keyword else analysis

            data_dict = {
                "date": pd.to_datetime(record["date"]).date() if isinstance(record["date"], str) else record["date"],
                "section": df_row.get("Section"),
                "board": df_row.get("Board"),
                "stock": rec_name,
                "code": record.get("code", ""),
                "last_price": record.get("last_price"),
                "change_rate": record.get("change_rate"),
                "lock_ratio": record.get("lock_ratio"),
                "turnover": record.get("turnover"),
                "market_capital": record.get("market_capital"),
                "total_capital": record.get("total_capital"),
                "d_time_first": record.get("d_time_first"),
                "d_time_last": record.get("d_time_last"),
                "analysis": combined_keyword,
                "highlight": df_row.get("Highlight")
            }
            data_dict_list.append(data_dict)
        except Exception as e:
            logger.error(f"Failed to upsert action data record: {record}; error: {e}")
    n = repo.delete_by_date(datetime.strptime(date, "%Y-%m-%d"))
    logger.info(f"deleted old {n} records.")
    repo.upsert_action_data_batch(data_dict_list)
    logger.info("Action data upserted to database.")


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

def _save_stockstats(rec_texts: list[str], market_number: dict, date):
    """
    1. 从数据库中读取最近90天的数据，画出曲线图：
        - 破板率单独画在一张图上；
        - 涨停数、跌停数、连板数、破板数共用一个y轴（左），上涨家数/下跌家数共用一个y轴（右）
    2. 新数据写入数据库表 stock_stats
    """

    # 数据抽取
    max_even, max_break = _get_maxEven_maxBreak(rec_texts)
    trendings = _get_trendings(rec_texts)

    # ===保存新数据到数据库 ===
    db_manager = get_db_manager()
    repo = StockStatsRepository(db_manager)

    new_stats = {
        "date": date,
        "up_limit": trendings["up"],
        "down_limit": trendings["down"],
        "even": trendings["even"],
        "break_rate": trendings["break_rate"],
        "up_limit_st": int(market_number.get("up_limit") or 0),
        "down_limit_st": int(market_number.get("down_limit") or 0),
        "up_fluctuation": int(market_number.get("up_fluctuation") or 0),
        "down_fluctuation": int(market_number.get("down_fluctuation") or 0),
        "max_even": int(max_even),
        "max_break": int(max_break),
    }
    repo.add_market_stats(new_stats)


def excel_flow(market_number: dict, records_list: list[dict], date):
    scraped_dir = "images"
    img_path = f"{scraped_dir}/Image.png"
    if not os.path.isfile(img_path):
        logger.error(f"Image file does not exist: {img_path}")
        return None
    try:
        path = preprocess_image(img_path)
        rec_texts = ocr_image_safe(path)
        _normalize_text(rec_texts)
        _save_actionData(rec_texts, "excel", date, records_list)
        _save_stockstats(rec_texts, market_number, date)
    except Exception as e:
        logger.error(f"Error while OCR: {e}")
        raise RuntimeError(f"Error while OCR: {e}")

# if __name__ == "__main__":
#     excel_flow({}, "2026-02-02")