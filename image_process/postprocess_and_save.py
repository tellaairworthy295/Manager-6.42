import os
import re
from datetime import datetime, timedelta

import phoenixc
import unicodedata
import pandas as pd
from utils.database import StockRepository, StockStatsRepository, get_db_manager, ActionLimitDataRepository, \
    HistoryKChartRepository, RealTimeChartRepository
from .preprocess import preprocess_image
from .ocr_api import ocr_image_safe
# Configure loguru for to_excel module
from utils.logging_config import get_stock_logger

# 2️⃣ Add file logger (safe, no buffering issues)
logger = get_stock_logger()


def _normalize_text(text: list[str], space: bool = False):
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


def _parse_actionData_to_df(rec_texts: list[str], cols: list[str]) -> pd.DataFrame:
    """
    Parse rec_texts into DataFrame for action data.
    Returns the DataFrame with expected columns.
    """
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
                    # m_first_board = re.match(r"^(\d+天\d+板)$", chunk[0].strip())
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

                    data.append([section_name] + chunk)
                    i += skip
                else:
                    break
        else:
            i += 1

    if not data:
        logger.error("No valid data to save.")
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(data, columns=cols)
    return df


def _save_actionData_excel(df: pd.DataFrame, excel_path: str, date: str):
    """
    Save DataFrame to Excel file.
    """
    os.makedirs(excel_path, exist_ok=True)
    excel_file = os.path.join(excel_path, f"actionData_{date}.xlsx")
    df.to_excel(excel_file, index=False)
    logger.info(f"Saved action data to Excel: {excel_file}")


def _save_actionData_db(df: pd.DataFrame, date: str, flag: bool):
    """
    Save DataFrame and records_list to database.
    Each record is a dict with date, section, board, code, market_capital, turnover_abs, analysis fields.
    Code field (six digits) should be extracted via extract_six_digit_code before upsert.
    """

    def extract_six_digit_code(code: str):
        if not isinstance(code, str):
            return None
        match = re.search(r"\d{6}", code)
        return match.group(0) if match else None

    date_obj = datetime.strptime(date, "%Y-%m-%d")
    db_manager = get_db_manager()
    repo_al = ActionLimitDataRepository(db_manager)

    # build dicts for batch upsert
    data_dict_list = []

    for _, row in df.iterrows():
        code_val = extract_six_digit_code(row.get("code", ""))
        analysis = "韭研:\n" + row.get("analysis") + "\n"
        record = {
            "date": date_obj,
            "section": row.get("section"),
            "board": row.get("board"),
            "code": code_val,
            "market_capital": row.get("market_capital"),
            "turnover_abs": row.get("turnover_abs"),
            "analysis": analysis,
        }
        data_dict_list.append(record)

    if not flag:
        n = repo_al.delete_by_date(datetime.strptime(date, "%Y-%m-%d"))
        logger.info(f"deleted old {n} records.")
        repo_al.insert_action_data_batch(data_dict_list)
        return

    repo_al.update_action_data_batch(data_dict_list)
    logger.info("Action data upserted to database.")


def _save_actionData(rec_texts: list[str], excel_path: str, date: str, flag: bool):
    """
    Separates excel and database operations for saving OCR-recognized texts.
    """
    # Expected columns
    cols = ["section", "board", "code", "stock", "time", "market_capital", "turnover_abs", "analysis"]
    df = _parse_actionData_to_df(rec_texts, cols)
    if df.empty:
        logger.error("No valid data to save.")
        return
    _save_actionData_excel(df, excel_path, date)
    _save_actionData_db(df, date, flag)


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
        "limit_up": r"涨停(\d+)家",
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


def _save_chart_data(date: str):
    phoenixc.init("guhao_ind", "kQ4@ks0u", ("10.29.92.42", 9081))

    end_date = datetime.strptime(date, "%Y-%m-%d").date()
    start_date = (end_date - timedelta(days=366)).strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")

    db_manager = get_db_manager()
    repo_stock = StockRepository(db_manager)
    repo_k_chart = HistoryKChartRepository(db_manager)
    repo_real_time_chart = RealTimeChartRepository(db_manager)

    # -----------------------------
    # helpers
    # -----------------------------
    def add_exchange_suffix(code: str) -> str:
        if not code or len(code) < 6:
            raise ValueError(f"not a valid code format: {code}")

        first_digit = code[0]
        if first_digit in ("0", "3"):
            return f"{code}.SZ"
        if first_digit == "6":
            return f"{code}.SH"
        if first_digit in ("4", "8", "9"):
            return f"{code}.BJ"

        raise ValueError(f"unknown code format: {code}")

    # -----------------------------
    # preprocess codes
    # -----------------------------
    codes = repo_stock.get_codes_by_date(end_date)
    codes = [add_exchange_suffix(code) for code in codes]

    # -----------------------------
    # delete existing records (once)
    # -----------------------------
    repo_real_time_chart.delete_rt_by_date(end_date)

    # -----------------------------
    # main loop (per code batching)
    # -----------------------------
    for code in codes:
        # if code != '603533.SH':
        #     continue
        logger.info("processing %s", code)
        repo_k_chart.delete_kchart_by_code(code)
        # =============================
        # Daily K data (batch per code)
        # =============================
        try:
            k_df = phoenixc.get_price(
                unified_code=code,
                start_date=start_date,
                end_date=end_date_str,
                frequency="1d",
            )
        except Exception:
            logger.exception("failed to fetch daily k for %s", code)
            continue

        if isinstance(k_df, pd.DataFrame) and not k_df.empty:
            k_df = k_df.reset_index()

            kcharts: list[dict] = []
            for _, row in k_df.iterrows():
                trading_date = row.get("trading_date")
                if pd.isna(trading_date):
                    continue

                record = {
                    "code": row["unified_code"],
                    "date": trading_date,
                    "open": row["open"],
                    "close": row["close"],
                    "high": row["high"],
                    "low": row["low"],
                    "volume": row["volume"],
                }

                if any(pd.isna(record[f]) for f in ("open", "close", "high", "low", "volume")):
                    continue

                kcharts.append(record)

            if kcharts:
                repo_k_chart.save_kcharts(kcharts)
        else:
            logger.warning("no daily k data for %s", code)

        # =============================
        # Tick data (chunked batch)
        # =============================
        try:
            tick_df = phoenixc.get_price(
                unified_code=code,
                start_date=end_date_str,
                end_date=end_date_str,
                frequency="1m",
                time_slice=("09:25:00", "15:00"),
            )
        except Exception:
            logger.exception("failed to fetch tick data for %s", code)
            continue

        if isinstance(tick_df, pd.DataFrame) and not tick_df.empty:
            tick_df = tick_df.reset_index()

            # Process cumulative volume to per-tick volume
            # Assume the column is named "volume"
            # tick_df["per_tick_volume"] = tick_df["volume"].diff().fillna(tick_df["volume"])
            # # Sometimes the first volume can be negative or wrong after diff, make sure all >=0
            # tick_df["per_tick_volume"] = tick_df["per_tick_volume"].clip(lower=0)

            rt_charts: list[dict] = []
            for idx, row in tick_df.iterrows():
                data_time = row.get("data_time")
                if pd.isna(data_time):
                    continue

                record = {
                    "code": row["unified_code"],
                    "date": end_date,
                    "data_time": data_time,
                    "close": row["close"],
                    "pre_close": row["pre_close"],
                    "change_rate": (row["close"] - row["pre_close"]) / row["pre_close"],
                    "volume": row["volume"] / 100,
                }

                if any(pd.isna(record[f]) for f in ("volume", "close", "pre_close")):
                    continue

                rt_charts.append(record)

            if rt_charts:
                repo_real_time_chart.save_realtime_charts(rt_charts)
        else:
            logger.warning("no tick data for %s", code)


def main_flow(market_number: dict, date, flag: bool = False):
    scraped_dir = "images"
    img_path = f"{scraped_dir}/Image.png"

    path = preprocess_image(img_path)
    rec_texts = ocr_image_safe(path)
    if not rec_texts:
        raise RuntimeError("Failed to parse image, no text recognized.")
    _normalize_text(rec_texts)
    _save_actionData(rec_texts, "excel", date, flag)
    _save_stockstats(rec_texts, market_number, date)
    _save_chart_data(date)



if __name__ == "__main__":
    main_flow({}, "2026-02-12", True)
