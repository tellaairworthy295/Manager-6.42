import asyncio
import random
from datetime import datetime, time
import requests

# Configuration
API_URL = "http://localhost:5000/news/scrape_news"
search_query = 'site:bloomberg.com/news/articles China OR "Hong Kong" OR AI'

def get_rate_limit():
    """Returns 6 for weekends, 4 for weekdays."""
    today = datetime.now()
    return 6 if today.weekday() >= 5 else 4

def get_wait_time():
    """
    Optimized Strategy:
    - Golden Hours (High Freq): 
        - Weekdays: 20:30 - 09:30 (Covers US Open, Euro Close, US Data releases)
        - Weekends: 20:00 - 09:30 (Slightly relaxed start for weekends)
    - Daytime (Medium Freq): 
        - 09:30 - 20:30 (Covers Asia Open, Euro Open, US Pre-market)
    - Early Morning (Low-Medium Freq): 
        - 07:00 - 09:30 (Optional: Can be merged into Daytime or kept separate)
    
    For simplicity and maximum coverage, we define:
    - High Freq Night: 20:30 to 09:30
    - Medium Freq Day: 09:30 to 20:30
    """
    now = datetime.now()
    weekday = now.weekday()
    current_time = now.time()
    
    is_weekend = weekday >= 5
    
    # Define boundaries
    # Start of Daytime: 09:30
    day_start = time(9, 30)
    # End of Daytime: 20:30 (US data release time)
    day_end = time(20, 30)
    
    # Determine if currently in Daytime window
    # Logic: If time is between 09:30 and 20:30
    is_daytime = day_start <= current_time < day_end

    # 1. Calculate Base Wait Time (The "Night/High Frequency" baseline)
    if is_weekend:
        base_min, base_max = 26, 54  # 30-60 mins
    else:
        base_min, base_max = 8, 18   # 7-19 mins
    
    base_wait_seconds = random.randint(base_min * 60, base_max * 60)

    # 2. Apply Multiplier
    if is_daytime:
        # Daytime: Medium Frequency
        if is_weekend:
            multiplier = 1.5 
            period_desc = "Weekend Day (09:30-20:30)"
        else:
            multiplier = 1.5
            period_desc = "Weekday Day (09:30-20:30)"
        
        final_wait_time = int(base_wait_seconds * multiplier)
    else:
        # Nighttime (including 20:30-21:00 crucial window): High Frequency
        final_wait_time = base_wait_seconds
        period_desc = "Global Peak Night (20:30-09:30)"
    
    return final_wait_time, period_desc

def get_payload_with_dynamic_rate_limit():
    current_rate_limit = get_rate_limit()
    return {
        "requests": [{
            "query": search_query,
            "site": "www.bloomberg.com/news/articles",
            "source": "bloomberg",
            "rate_limit": current_rate_limit
        }]
    }

def send_scrape_request():
    payload_to_send = get_payload_with_dynamic_rate_limit()
    current_rate_limit = get_rate_limit()
    
    try:
        response = requests.post(API_URL, json=payload_to_send)
        if response.status_code == 200:
            result = response.json()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] OK | Limit={current_rate_limit} | ID: {result.get('task_id')}")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] FAIL | Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR | {e}")

async def main():
    print("Starting Optimized Scheduler...")
    print("Strategy: High freq during US/Euro overlap (20:30-09:30), Medium freq during Asia/Euro day (09:30-20:30).")
    
    while True:
        send_scrape_request()
        wait_time, period_desc = get_wait_time()
        
        minutes = wait_time // 60
        print(f"-> Mode: {period_desc}. Waiting ~{minutes} mins.")
        
        await asyncio.sleep(wait_time)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")