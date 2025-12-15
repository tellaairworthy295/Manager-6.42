# page_factory.py
def new_stealth_page(context):
    page = context.new_page()

    # Kill target=_blank popups
    page.on("popup", lambda popup: popup.close())

    page.set_extra_http_headers({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.7499.40 Safari/537.36"
        )
    })

    page.add_init_script("""
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
    """)

    return page
