import requests
import time

# Configuration
API_URL = "http://127.0.0.1:49862"
ROTATE_INTERVAL_SECONDS = 60 * 30  # Change this to how often you want to rotate (in seconds)
SECRET = "f590af10-6229-4526-9a3c-9159cb508491"

# Headers for API authentication
HEADERS = {"Authorization": f"Bearer {SECRET}"}

# Define your custom wait times in SECONDS
# (e.g., 10 minutes = 10 * 60 = 600 seconds)
REGION_INTERVALS = {
    "Hong Kong": 20 * 60,
    "Taiwan": 60 * 60,
    "USA": 20 * 60,
    "Japan": 30 * 60,
    "UK": 30 * 60,
    "Singapore": 30 * 60,
    "Malaysia": 30 * 60,
    "Turkey": 45 * 60,
    "Argentina": 45 * 60
}

# Default wait time if the country is not in the list above
DEFAULT_INTERVAL = 5 * 60  # 5 minutes


def get_proxy_data():
    """Fetch all proxies and groups from the Clash API."""
    try:
        response = requests.get(f"{API_URL}/proxies", headers=HEADERS)
        response.raise_for_status()
        return response.json().get('proxies', {})
    except Exception as e:
        print(f"❌ Could not connect to Clash. Is Clash for Windows running? Error: {e}")
        return None


def find_global_proxies(proxies_data):
    """Finds only the real, physical non-SS proxies inside the GLOBAL tab."""
    target_group = "GLOBAL"

    if target_group not in proxies_data:
        return None, []

    all_items = proxies_data[target_group]['all']
    valid_proxies = []

    for item_name in all_items:
        item_info = proxies_data.get(item_name, {})
        item_type = item_info.get('type', '')

        # Filter out Groups and System built-ins
        if item_type in ['Selector', 'URLTest', 'Fallback', 'LoadBalance', 'Direct', 'Reject', 'Pass']:
            continue
        if item_name in ['DIRECT', 'REJECT'] or item_name.startswith('[SS]'):
            continue

        valid_proxies.append(item_name)

    return target_group, valid_proxies


def switch_proxy(proxy_name):
    """Tell Clash to switch the GLOBAL group to a specific proxy."""
    try:
        url = f"{API_URL}/proxies/GLOBAL"
        payload = {"name": proxy_name}

        # Included HEADERS here to fix the 401 Unauthorized error
        response = requests.put(url, json=payload, headers=HEADERS)
        response.raise_for_status()
        print(f"✅ Successfully changed Global IP to: {proxy_name}")
    except Exception as e:
        print(f"❌ Failed to switch proxy: {e}")


def get_wait_time(proxy_name):
    """Determine how long to wait based on the proxy's name."""
    # Check if any of our defined regions are in the proxy name
    for region, wait_time in REGION_INTERVALS.items():
        if region in proxy_name:
            return wait_time

    # If no match is found (e.g., Singapore, Malaysia), use the default
    return DEFAULT_INTERVAL


def main():
    print("🔍 Connecting to Clash API...")
    proxies_data = get_proxy_data()

    if not proxies_data:
        return

    group_name, proxy_list = find_global_proxies(proxies_data)

    if not proxy_list:
        print("❌ Could not find any valid proxies in the GLOBAL group.")
        return

    print(f"🎯 Target Group: {group_name} (全局模式)")
    print(f"🌐 Found {len(proxy_list)} high-quality proxies. Starting rotation...\n")

    index = 0
    while True:
        current_proxy = proxy_list[index]

        # 1. Switch the proxy
        switch_proxy(current_proxy)

        # 2. Calculate the wait time for this specific proxy
        wait_seconds = get_wait_time(current_proxy)
        wait_minutes = wait_seconds / 60

        print(f"⏳ Next rotation in {wait_minutes:.1f} minutes ({wait_seconds}s). Waiting...")
        time.sleep(wait_seconds)
        print("-" * 50)  # Just a visual separator for the logs

        # 3. Move to next proxy
        index = (index + 1) % len(proxy_list)


if __name__ == "__main__":
    main()