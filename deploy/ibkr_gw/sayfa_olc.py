"""IBKR giris sayfasinin YAPISINI olcer (giris YAPMAZ): gorunur input/buton."""
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(ignore_https_errors=True)
    pg.goto("https://127.0.0.1:5000/", wait_until="load", timeout=60000)
    pg.wait_for_timeout(5000)
    print("URL:", pg.url, "| BASLIK:", pg.title())
    for el in pg.query_selector_all("input, button, select"):
        if el.is_visible():
            print(el.evaluate("e => e.tagName"), {k: el.get_attribute(k) for k in
                  ("id", "name", "type", "placeholder", "class") if el.get_attribute(k)},
                  (el.inner_text() or "")[:40].strip())
    pg.screenshot(path="/tmp/giris_sayfasi.png", full_page=True)
    b.close()
