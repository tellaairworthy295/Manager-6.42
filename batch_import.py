#!/usr/bin/env python3
"""
Batch CSV to QuestDB ILP Ingester - Separate Tables for Futures & Options
- Routes data to appropriate table based on InstrumentID format
- Futures: contracts without option type/strike (e.g., IF2512, IC2512)
- Options: contracts with option type and strike (e.g., HO2602-C-2650)
"""

import csv
import socket
import os
import re
import glob
from datetime import datetime
from typing import List, Tuple, Optional, Dict
import argparse


def parse_instrument_id(instrument_id: str) -> Dict|None:
    """
    Parse InstrumentID and determine type.
    Returns dict with 'type' and component fields.

    Examples:
        "HO2602-C-2650" -> {type: 'option', root: "HO2602", opt_type: "C", strike: 2650}
        "IF2512"        -> {type: 'future', root: "IF2512"}
    """
    # Pattern for options: Product+YY+MM + "-" + C/P + "-" + Strike
    option_pattern = r'^([A-Z]{2,})(\d{4})-([CP])-(\d+)$'
    future_pattern = r'^([A-Z]{2,})(\d{4})$'
    match_option = re.match(option_pattern, instrument_id)
    match_future = re.match(future_pattern, instrument_id)
    if match_option:
        product, session, opt_type, strike = match_option.groups()
        return {
            'type': 'option',
            'product': product, # e.g., "HO"
            'root': product + session,  # e.g., "HO2602"
            'opt_type': opt_type,  # "C" or "P"
            'strike': int(strike)
        }
    elif match_future:
        product, session = match_future.groups()
        # Futures contract: just the root (e.g., "IF2512")
        return {
            'type': 'future',
            'product': product,
            'root': instrument_id
        }
    return None


def csv_to_ilp_row_futures(row: dict, timestamp_ns: int) -> Optional[str]:
    """Convert CSV row to ILP format for futures table"""

    table_name = "futures_data"
    parts = parse_instrument_id(row.get('InstrumentID', ''))

    if not parts:
        return None
    
    # === TAGS ===
    tags = [f"Prodcut={parts['Product']}", f"Contract={parts['root']}"]

    # === FIELDS ===
    fields = []

    # Store full InstrumentID as field
    fields.append(f'InstrumentID="{row["InstrumentID"]}"')

    # Price fields (DOUBLE)
    price_fields = [
        'PreSettlementPrice', 'PreClosePrice', 'PreOpenInterest',
        'OpenPrice', 'HighestPrice', 'LowestPrice', 'ClosePrice',
        'UpperLimitPrice', 'LowerLimitPrice', 'SettlementPrice', 'LastPrice'
    ]

    for field in price_fields:
        val = row.get(field, '')
        if val and val != '0.0000':
            try:
                fields.append(f"{field}={float(val):.4f}")
            except ValueError:
                pass

    # Volume and OpenInterest (LONG integers)
    if row.get('Volume', '0') != '0':
        fields.append(f"Volume={int(row['Volume'])}i")

    if row.get('OpenInterest', '0') != '0':
        fields.append(f"OpenInterest={int(row['OpenInterest'])}i")

    # Bid/Ask levels
    for level in range(1, 6):
        bid_price = row.get(f'BidPrice{level}', '')
        bid_vol = row.get(f'BidVolume{level}', '')
        ask_price = row.get(f'AskPrice{level}', '')
        ask_vol = row.get(f'AskVolume{level}', '')

        if bid_price and bid_price != '0.0000':
            fields.append(f"BidPrice{level}={float(bid_price):.4f}")
        if bid_vol and bid_vol != '0':
            fields.append(f"BidVolume{level}={int(bid_vol)}i")
        if ask_price and ask_price != '0.0000':
            fields.append(f"AskPrice{level}={float(ask_price):.4f}")
        if ask_vol and ask_vol != '0':
            fields.append(f"AskVolume{level}={int(ask_vol)}i")

    if not fields:
        return None

    tag_string = f"{table_name},{','.join(tags)}"
    field_string = ",".join(fields)

    return f"{tag_string} {field_string} {timestamp_ns}\n"


def csv_to_ilp_row_options(row: dict, timestamp_ns: int) -> Optional[str]:
    """Convert CSV row to ILP format for options table"""

    table_name = "options_data"
    parts = parse_instrument_id(row.get('InstrumentID', ''))

    if not parts:
        return None
    # === TAGS ===
    tags = [f"Prodcut={parts['Product']}", f"Contract={parts['root']}"]

    # === FIELDS ===
    fields = []

    # Store full InstrumentID as field
    fields.append(f'InstrumentID="{row["InstrumentID"]}"')

    # Option-specific fields
    is_call_val = "true" if parts['opt_type'] == 'C' else "false"
    fields.append(f"IsCall={is_call_val}")
    fields.append(f"Strike={parts['strike']}i")

    # Price fields (DOUBLE)
    price_fields = [
        'PreSettlementPrice', 'PreClosePrice', 'PreOpenInterest',
        'OpenPrice', 'HighestPrice', 'LowestPrice', 'ClosePrice',
        'UpperLimitPrice', 'LowerLimitPrice', 'SettlementPrice', 'LastPrice'
    ]

    for field in price_fields:
        val = row.get(field, '')
        if val and val != '0.0000':
            try:
                fields.append(f"{field}={float(val):.4f}")
            except ValueError:
                pass

    # Volume and OpenInterest (LONG integers)
    if row.get('Volume', '0') != '0':
        fields.append(f"Volume={int(row['Volume'])}i")

    if row.get('OpenInterest', '0') != '0':
        fields.append(f"OpenInterest={int(row['OpenInterest'])}i")

    # Bid/Ask levels
    for level in range(1, 6):
        bid_price = row.get(f'BidPrice{level}', '')
        bid_vol = row.get(f'BidVolume{level}', '')
        ask_price = row.get(f'AskPrice{level}', '')
        ask_vol = row.get(f'AskVolume{level}', '')

        if bid_price and bid_price != '0.0000':
            fields.append(f"BidPrice{level}={float(bid_price):.4f}")
        if bid_vol and bid_vol != '0':
            fields.append(f"BidVolume{level}={int(bid_vol)}i")
        if ask_price and ask_price != '0.0000':
            fields.append(f"AskPrice{level}={float(ask_price):.4f}")
        if ask_vol and ask_vol != '0':
            fields.append(f"AskVolume{level}={int(ask_vol)}i")

    if not fields:
        return None

    tag_string = f"{table_name},{','.join(tags)}"
    field_string = ",".join(fields)

    return f"{tag_string} {field_string} {timestamp_ns}\n"


def parse_timestamp(row: dict, file_date: str = None) -> int:
    """Parse timestamp from CSV TradingDay + UpdateTime + UpdateMillisec"""

    date_str = row.get('TradingDay', '')
    time_str = row.get('UpdateTime', '')
    ms_str = row.get('UpdateMillisec', '0')

    if not date_str and file_date:
        date_str = file_date

    if date_str and time_str:
        try:
            if len(date_str) == 8 and date_str.isdigit():
                year = date_str[:4]
                month = date_str[4:6]
                day = date_str[6:8]
            else:
                year, month, day = date_str.split('-')[:3]

            datetime_str = f"{year}-{month}-{day} {time_str}"
            dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
            timestamp_ns = int(dt.timestamp() * 1_000_000_000)

            if ms_str and ms_str != '0':
                timestamp_ns += int(ms_str) * 1_000_000

            return timestamp_ns
        except (ValueError, IndexError) as e:
            print(f"  Warning: Timestamp parse error: {e}")

    if file_date:
        try:
            dt = datetime.strptime(file_date, "%Y%m%d")
            return int(dt.timestamp() * 1_000_000_000)
        except:
            pass

    return int(datetime.now().timestamp() * 1_000_000_000)


def send_batch(host: str, port: int, lines: List[str]) -> bool:
    """Send batch via TCP with retry logic"""
    if not lines:
        return True

    max_retries = 3
    for attempt in range(max_retries):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)
            sock.connect((host, port))
            message = "".join(lines)
            sock.sendall(message.encode('utf-8'))
            sock.close()
            return True
        except socket.timeout:
            print(f"    Timeout on attempt {attempt + 1}/{max_retries}")
            continue
        except Exception as e:
            print(f"    Socket error on attempt {attempt + 1}: {e}")
            continue
        finally:
            if sock:
                try:
                    sock.close()
                except:
                    pass

    return False


def extract_date_from_filename(filename: str) -> Optional[str]:
    """Extract date from filename like 'CF_20251103.csv' -> '20251103'"""
    match = re.search(r'(\d{8})\.csv$', filename)
    if match:
        return match.group(1)
    return None


def process_csv_file(filepath: str, host: str, port: int, batch_size: int) -> Tuple[int, int, int, int, int]:
    """
    Process a single CSV file.
    Returns: (total_rows, futures_inserted, options_inserted, errors, skipped)
    """

    filename = os.path.basename(filepath)
    file_date = extract_date_from_filename(filename)

    print(f"\n📄 Processing {filename} (Date: {file_date})")

    futures_batch = []
    options_batch = []
    total_rows = 0
    futures_inserted = 0
    options_inserted = 0
    error_rows = 0
    skipped_rows = 0

    stats = {'future': 0, 'option': 0}

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            f.seek(0)
            delimiter = '\t' if '\t' in first_line else ','
            reader = csv.DictReader(f, delimiter=delimiter)

            required_cols = ['InstrumentID', 'UpdateTime']
            missing_cols = [col for col in required_cols if col not in reader.fieldnames]
            if missing_cols:
                print(f"  ⚠️  Skipping: Missing required columns: {missing_cols}")
                return (0, 0, 0, 1, 0)

            for row_num, row in enumerate(reader, 1):
                total_rows += 1

                try:
                    timestamp_ns = parse_timestamp(row, file_date)
                    instrument_id = row.get('InstrumentID', '')
                    parts = parse_instrument_id(instrument_id)

                    if parts['type'] == 'future':
                        ilp_line = csv_to_ilp_row_futures(row, timestamp_ns)
                        if ilp_line:
                            futures_batch.append(ilp_line)
                            stats['future'] += 1
                        else:
                            skipped_rows += 1
                    else:  # option
                        ilp_line = csv_to_ilp_row_options(row, timestamp_ns)
                        if ilp_line:
                            options_batch.append(ilp_line)
                            stats['option'] += 1
                        else:
                            skipped_rows += 1

                    # Send futures batch when full
                    if len(futures_batch) >= batch_size:
                        if send_batch(host, port, futures_batch):
                            futures_inserted += len(futures_batch)
                            futures_batch = []
                        else:
                            print(f"  ❌ Failed to send futures batch at row {total_rows}")
                            error_rows += len(futures_batch)
                            futures_batch = []

                    # Send options batch when full
                    if len(options_batch) >= batch_size:
                        if send_batch(host, port, options_batch):
                            options_inserted += len(options_batch)
                            options_batch = []
                        else:
                            print(f"  ❌ Failed to send options batch at row {total_rows}")
                            error_rows += len(options_batch)
                            options_batch = []

                except Exception as e:
                    error_rows += 1
                    if error_rows <= 5:
                        print(f"  ⚠️  Row {row_num} error: {e}")

                if total_rows % 10000 == 0:
                    print(f"    Progress: {total_rows} rows (F:{stats['future']} O:{stats['option']})...", end='\r')

            # Send remaining batches
            if futures_batch:
                if send_batch(host, port, futures_batch):
                    futures_inserted += len(futures_batch)
                else:
                    error_rows += len(futures_batch)

            if options_batch:
                if send_batch(host, port, options_batch):
                    options_inserted += len(options_batch)
                else:
                    error_rows += len(options_batch)

    except Exception as e:
        print(f"  ❌ File error: {e}")
        return (total_rows, futures_inserted, options_inserted, error_rows + 1, skipped_rows)

    print(f"  ✅ Done: Futures: {futures_inserted}/{stats['future']}, Options: {options_inserted}/{stats['option']}, "
          f"Errors: {error_rows}, Skipped: {skipped_rows}")
    return (total_rows, futures_inserted, options_inserted, error_rows, skipped_rows)


def create_tables_if_not_exists(host: str, port_http: int = 19000):
    """Create both tables if they don't exist"""

    # Futures table schema
    futures_sql = """
                    CREATE TABLE IF NOT EXISTS futures_data (
                        -- Categorical identifiers (optimized with SYMBOL)
                        Product SYMBOL,
                        Contract SYMBOL,
                        InstrumentID VARCHAR,
                        
                        -- Price Data
                        PreSettlementPrice DOUBLE,
                        PreClosePrice DOUBLE,
                        PreOpenInterest DOUBLE,
                        OpenPrice DOUBLE,
                        HighestPrice DOUBLE,
                        LowestPrice DOUBLE,
                        ClosePrice DOUBLE,
                        UpperLimitPrice DOUBLE,
                        LowerLimitPrice DOUBLE,
                        SettlementPrice DOUBLE,
                        LastPrice DOUBLE,
                        
                        -- Volume/Interest
                        Volume LONG,
                        OpenInterest LONG,
                        
                        -- Order Book Data
                        BidPrice1 DOUBLE, BidVolume1 LONG,
                        AskPrice1 DOUBLE, AskVolume1 LONG,
                        BidPrice2 DOUBLE, BidVolume2 LONG,
                        AskPrice2 DOUBLE, AskVolume2 LONG,
                        BidPrice3 DOUBLE, BidVolume3 LONG,
                        AskPrice3 DOUBLE, AskVolume3 LONG,
                        BidPrice4 DOUBLE, BidVolume4 LONG,
                        AskPrice4 DOUBLE, AskVolume4 LONG,
                        BidPrice5 DOUBLE, BidVolume5 LONG,
                        AskPrice5 DOUBLE, AskVolume5 LONG,
                        
                        -- Timestamp (Designated)
                        ts TIMESTAMP
                        
                    ) TIMESTAMP(ts)                 -- Designated timestamp column
                    PARTITION BY DAY                -- Daily partitions for optimal time-based queries
                    WAL;                            -- Write-Ahead Log enabled for better concurrency and data safety
                  """

    # Options table schema
    options_sql = """
                    CREATE TABLE IF NOT EXISTS options_data (
                        -- Categorical identifiers (optimized with SYMBOL)
                        Product SYMBOL,
                        InstrumentRoot SYMBOL,
                        InstrumentID VARCHAR,                 -- Full option ID, high cardinality
                        
                        -- Option-specific fields
                        IsCall BOOLEAN,                       -- True=CALL, False=PUT
                        Strike LONG,                          -- Strike price (as integer cents/pips)
                        
                        -- Price Data
                        PreSettlementPrice DOUBLE,
                        PreClosePrice DOUBLE,
                        PreOpenInterest DOUBLE,
                        OpenPrice DOUBLE,
                        HighestPrice DOUBLE,
                        LowestPrice DOUBLE,
                        ClosePrice DOUBLE,
                        UpperLimitPrice DOUBLE,
                        LowerLimitPrice DOUBLE,
                        SettlementPrice DOUBLE,
                        LastPrice DOUBLE,
                        
                        -- Volume/Interest
                        Volume LONG,
                        OpenInterest LONG,
                        
                        -- Order Book Data (Level 1-5)
                        BidPrice1 DOUBLE, BidVolume1 LONG,
                        AskPrice1 DOUBLE, AskVolume1 LONG,
                        BidPrice2 DOUBLE, BidVolume2 LONG,
                        AskPrice2 DOUBLE, AskVolume2 LONG,
                        BidPrice3 DOUBLE, BidVolume3 LONG,
                        AskPrice3 DOUBLE, AskVolume3 LONG,
                        BidPrice4 DOUBLE, BidVolume4 LONG,
                        AskPrice4 DOUBLE, AskVolume4 LONG,
                        BidPrice5 DOUBLE, BidVolume5 LONG,
                        AskPrice5 DOUBLE, AskVolume5 LONG,
                        
                        -- Timestamp (Designated)
                        ts TIMESTAMP
                        
                    ) TIMESTAMP(ts)                 -- Designated timestamp column
                    PARTITION BY DAY                -- Daily partitions for time-based queries
                    WAL;                            -- Write-Ahead Log enabled
                  """

    try:
        import requests

        # Create futures table
        response = requests.post(
            f'http://{host}:{port_http}/exec',
            json={'query': futures_sql},
            timeout=10
        )
        if response.status_code == 200:
            print("✅ Futures table schema verified/created")
        else:
            print(f"⚠️  Futures table issue: {response.text}")

        # Create options table
        response = requests.post(
            f'http://{host}:{port_http}/exec',
            json={'query': options_sql},
            timeout=10
        )
        if response.status_code == 200:
            print("✅ Options table schema verified/created")
        else:
            print(f"⚠️  Options table issue: {response.text}")

        return True
    except Exception as e:
        print(f"⚠️  Could not connect to HTTP endpoint: {e}")
        print("   Make sure QuestDB is running and tables exist, or create them manually")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Batch import CSV files to QuestDB - Separate Futures & Options tables',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
            Examples:
                python batch_import.py --dir /path/to/csvs
                python batch_import.py --dir . --pattern "CF_2026*.csv"
                python batch_import.py --dir . --dry-run
        """
    )
    parser.add_argument('--dir', default='.', help='Directory containing CSV files')
    parser.add_argument('--pattern', default='*.csv', help='File pattern to match')
    parser.add_argument('--host', default='10.28.71.160', help='QuestDB host')
    parser.add_argument('--ilp-port', type=int, default=19009, help='QuestDB ILP/TCP port')
    parser.add_argument('--http-port', type=int, default=19000, help='QuestDB HTTP port')
    parser.add_argument('--batch-size', type=int, default=1000, help='Batch size')
    parser.add_argument('--dry-run', action='store_true', help='Dry run - only show what would be processed')

    args = parser.parse_args()

    search_pattern = os.path.join(args.dir, args.pattern)
    csv_files = sorted(glob.glob(search_pattern))

    if not csv_files:
        print(f"No CSV files found matching: {search_pattern}")
        return

    print(f"\n{'=' * 60}")
    print(f"📊 QuestDB Batch CSV Importer - Futures & Options (Separate Tables)")
    print(f"{'=' * 60}")
    print(f"Directory: {args.dir}")
    print(f"Files found: {len(csv_files)}")
    print(f"QuestDB: {args.host}:{args.ilp_port}")
    print(f"Batch size: {args.batch_size}")
    print(f"{'=' * 60}")

    if args.dry_run:
        print("\n📋 DRY RUN - Files that would be processed:")
        for f in csv_files:
            file_date = extract_date_from_filename(os.path.basename(f))
            print(f"  - {os.path.basename(f)} (Date: {file_date})")
        return

    print("\n🔧 Setting up table schemas...")
    create_tables_if_not_exists(args.host, args.http_port)

    print("\n📥 Starting batch import...")
    total_files = len(csv_files)
    total_rows_all = 0
    total_futures_all = 0
    total_options_all = 0
    total_errors_all = 0
    total_skipped_all = 0

    start_time = datetime.now()

    for idx, csv_file in enumerate(csv_files, 1):
        print(f"\n[{idx}/{total_files}] ", end='')
        rows, futures_ins, options_ins, errors, skipped = process_csv_file(
            csv_file, args.host, args.ilp_port, args.batch_size
        )
        total_rows_all += rows
        total_futures_all += futures_ins
        total_options_all += options_ins
        total_errors_all += errors
        total_skipped_all += skipped

    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"\n{'=' * 60}")
    print(f"✅ IMPORT COMPLETE")
    print(f"{'=' * 60}")
    print(f"Files processed: {total_files}")
    print(f"Total rows read: {total_rows_all:,}")
    print(f"Futures inserted: {total_futures_all:,}")
    print(f"Options inserted: {total_options_all:,}")
    print(f"Total inserted: {total_futures_all + total_options_all:,}")
    print(f"Skipped (empty): {total_skipped_all:,}")
    print(f"Errors: {total_errors_all:,}")
    if total_rows_all > 0:
        print(f"Success rate: {((total_futures_all + total_options_all) / total_rows_all * 100):.1f}%")
        print(f"Throughput: {(total_futures_all + total_options_all) / elapsed:.0f} rows/second")
    print(f"Time elapsed: {elapsed:.1f} seconds")

    print(f"\n📊 Sample queries:")
    print(f"  -- Futures: SELECT * FROM futures_data WHERE InstrumentRoot = 'IF2512';")
    print(f"  -- Options: SELECT * FROM options_data WHERE InstrumentRoot = 'HO2602';")
    print(f"  -- Call options: SELECT * FROM options_data WHERE IsCall = true;")


if __name__ == "__main__":
    main()