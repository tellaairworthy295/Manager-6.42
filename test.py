import asyncio
import random
from datetime import datetime
import requests

# Configuration
API_URL = "http://localhost:5000/api/scrape_news"
REQUEST_PAYLOAD = {
    # Using your provided request structure
    "requests": [
        {"query": 'site:bloomberg.com/news/articles', "site": "www.bloomberg.com/news/articles", "source": "bloomberg"}
    ]
}


def send_scrape_request():
    """Sends a single request to the scraping API."""
    try:
        response = requests.post(API_URL, json=REQUEST_PAYLOAD)

        if response.status_code == 200:
            result = response.json()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Request sent successfully. Task ID: {result.get('task_id')}")
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

        # Generate a random delay between 300 (5 min) and 1000 seconds
        wait_time = random.randint(330, 1000)

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