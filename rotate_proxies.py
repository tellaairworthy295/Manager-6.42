import requests
import threading
import socket
import sys
from datetime import datetime
import random
# --- Configuration ---
API_URL = "http://127.0.0.1:50064"
SECRET = "f590af10-6229-4526-9a3c-9159cb508491"
HEADERS = {"Authorization": f"Bearer {SECRET}"}

REGION_INTERVALS = {
    "HK": 15 * 60,
    "TW": 25 * 60,
    "US": 20 * 60,
    "JP": 20 * 60,
    "KR": 20 * 60,
    "UK": 20 * 60,
    "SG": 25 * 60,
    "DE": 20 * 60,
    "FR": 20 * 60
}
DEFAULT_INTERVAL = 20 * 60

# --- IPC Setup ---
IPC_HOST = "127.0.0.1"
IPC_PORT = 50555
skip_wait_event = threading.Event()
shutdown_event = threading.Event()


def force_rotate():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b"ROTATE_NOW", (IPC_HOST, IPC_PORT))
        print("⚡ Sent rotation signal!\n")
    except Exception as e:
        print(f"❌ Failed to send rotation signal: {e}\n")


def ipc_server_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    try:
        sock.bind((IPC_HOST, IPC_PORT))
    except Exception as e:
        print(f"⚠️ Warning: Could not bind IPC server (Port {IPC_PORT} in use). Signal listening disabled.")
        return

    while not shutdown_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
            if data == b"ROTATE_NOW":
                print("\n🚨 Received external rotation signal! Skipping wait...\n")
                skip_wait_event.set()
        except socket.timeout:
            continue
        except OSError:
            if not shutdown_event.is_set():
                shutdown_event.set()
            break

    sock.close()


def get_proxy_data():
    try:
        response = requests.get(f"{API_URL}/proxies", headers=HEADERS)
        response.raise_for_status()
        return response.json().get('proxies', {})
    except Exception as e:
        print(f"❌ Could not connect to Clash. Error: {e}")
        return None


def find_global_proxies(proxies_data):
    target_group = "GLOBAL"
    if target_group not in proxies_data:
        return None, []

    all_items = proxies_data[target_group]['all']
    valid_proxies = []

    for item_name in all_items:
        item_info = proxies_data.get(item_name, {})
        item_type = item_info.get('type', '')

        if item_type in ['Selector', 'URLTest', 'Fallback', 'LoadBalance', 'Direct', 'Reject', 'Pass']:
            continue
        if item_name in ['DIRECT', 'REJECT', 'Proxy', 'Domestic', 'AsianTV', 'GlobalTV', 'Others', '故障转移', 'TaiShan Net'] or 'HK 0' in item_name  or 'HK 1' in item_name or 'KR' in item_name or 'JP 21' in item_name or 'JP 22' in item_name or "境外用户专用" in item_name \
                or "剩余" in item_name or "套餐到期" in item_name or item_name.startswith('[SS]') or item_name.startswith('IPV6') or item_name.startswith('直连') or item_name.startswith('*官网'):
            continue

        valid_proxies.append(item_name)

    return target_group, valid_proxies


def switch_proxy(proxy_name):
    try:
        url = f"{API_URL}/proxies/GLOBAL"
        payload = {"name": proxy_name}
        response = requests.put(url, json=payload, headers=HEADERS)
        response.raise_for_status()
        print(f"✅ [{datetime.now()}] Successfully changed Global IP to: {proxy_name}")
    except Exception as e:
        print(f"❌ Failed to switch proxy: {e}")


def get_wait_time(proxy_name):
    for region, wait_time in REGION_INTERVALS.items():
        if region in proxy_name:
            return random.randint(int(wait_time * 0.8), int(wait_time * 1.2))
    return DEFAULT_INTERVAL


def rotation_loop(proxy_list):
    """Runs in a BACKGROUND thread — no stdin touching here."""
    index = 0
    while not shutdown_event.is_set():
        current_proxy = proxy_list[index]
        switch_proxy(current_proxy)

        wait_seconds = get_wait_time(current_proxy)
        print(f"⏳ Next rotation in {wait_seconds / 60:.1f} min. Press ENTER to rotate instantly.")

        skip_wait_event.wait(timeout=wait_seconds)
        skip_wait_event.clear()

        if shutdown_event.is_set():
            break

        print("-" * 50)
        index = (index + 1) % len(proxy_list)


def main():
    print("🔍 Connecting to Clash API...")
    proxies_data = get_proxy_data()
    if not proxies_data:
        return

    group_name, proxy_list = find_global_proxies(proxies_data)
    if not proxy_list:
        print("❌ Could not find valid proxies.")
        return

    print(f"🎯 Target Group: {group_name}")
    print(f"🌐 Found {len(proxy_list)} proxies. Starting rotation...\n")
    print(proxy_list)
    threading.Thread(target=ipc_server_listener, daemon=True).start()

    # ✅ Rotation loop runs in background thread
    threading.Thread(target=rotation_loop, args=(proxy_list,), daemon=True).start()

    # ✅ stdin is owned exclusively by the MAIN thread — no lock contention
    try:
        while not shutdown_event.is_set():
            try:
                input()  # Blocks cleanly here with no competition
                if not shutdown_event.is_set():
                    force_rotate()
            except (EOFError, OSError):
                break
    except KeyboardInterrupt:
        pass  # Falls through to finally immediately — no deadlock
    finally:
        print("\n🛑 Shutting down gracefully...")
        shutdown_event.set()
        skip_wait_event.set()  # Unblocks rotation_loop if mid-wait
        print("👋 Proxy Manager stopped.")


if __name__ == "__main__":
    main()