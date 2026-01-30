import requests
requests.get(
    "https://ipv4.webshare.io/",
    proxies={
        "http": "http://kkfwbqem:u03nhy3u6js5@23.95.150.145:6114/",
        "https": "http://kkfwbqem:u03nhy3u6js5@23.95.150.145:6114/"
    }
).text
