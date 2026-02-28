import asyncio
import random
from datetime import datetime
import requests

# Configuration
API_URL = "http://localhost:5000/news/scrape_news"


def get_rate_limit():
    """
    Determines the rate limit based on whether it's a weekend or not.
    Returns 6 for weekends, 4 for weekdays.
    """
    today = datetime.now()
    if today.weekday() >= 5:  # Saturday (5) or Sunday (6)
        return 6
    else:
        return 4


def get_payload_with_dynamic_rate_limit():
    """
    Generates the REQUEST_PAYLOAD with the appropriate rate_limit for the current day.
    """
    current_rate_limit = get_rate_limit()

    return {
        "requests": [
            {
                "query": 'site:bloomberg.com/news/articles',
                "site": "www.bloomberg.com/news/articles",
                "source": "bloomberg",
                "rate_limit": current_rate_limit
            }
            # Add more requests here if needed, ensuring the 'rate_limit' is set correctly
            # {
            #     "query": 'site:another-site.com/news',
            #     "site": "www.another-site.com/news",
            #     "source": "another_source",
            #     "rate_limit": current_rate_limit # Use the same dynamic value
            # },
        ]
    }


def send_scrape_request():
    """Sends a single request to the scraping API with the current day's rate limit."""
    # Get the payload with the correct rate limit for today
    payload_to_send = get_payload_with_dynamic_rate_limit()

    try:
        response = requests.post(API_URL, json=payload_to_send)

        if response.status_code == 200:
            result = response.json()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Request sent successfully with rate_limit={get_rate_limit()}. Task ID: {result.get('task_id')}")
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Request failed with status code: {response.status_code}. Error: {response.text}")

    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
              f"An error occurred while making the request: {e}")


async def main():
    """Main loop to send requests periodically."""
    print(f"Starting periodic requests to {API_URL}...")
    print("Press Ctrl+C to stop.")

    while True:
        # Send the request
        send_scrape_request()

        # Determine the wait time based on the day of the week
        today = datetime.now()
        if today.weekday() >= 5:  # Weekend logic
            wait_time = random.randint(25 * 60, 55 * 60)  # 30-60 minutes
            print(f"It's the weekend. Waiting for {wait_time // 60} minutes...")
        else:  # Weekday logic
            wait_time = random.randint(6 * 60, 17 * 60)
            print(f"Waiting for {wait_time // 60} minutes and {wait_time % 60} seconds...")

        # Wait asynchronously for the specified time
        await asyncio.sleep(wait_time)


if __name__ == "__main__":
    try:
        # Run the asynchronous event loop
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScript stopped by user.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")