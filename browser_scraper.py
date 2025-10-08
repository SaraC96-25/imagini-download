# browser_scraper.py
# Versione completa con: login, chiusura modale Best Price, cookie banner,
# click sequenziale su tutti gli swatch con attesa immagine+label, salvataggio per colore.

from pathlib import Path
import re, urllib.parse, time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

SEL_SKU = "h2.prodCode"
SEL_MAIN_IMG = "#js_productMainPhoto img"
SEL_COLOR_LABEL = "p.colorLabel.js_searchable"
SEL_SWATCHES = "#js_availablecolorsheader .wrapperSwitchColore a.js_colorswitch"
SEL_HD = "a.js_downloadPhoto[href*='product_photo_download']"

def filename_sanitize(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name

def guess_ext_from_bytes(data: bytes, default=".jpg") -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and b"WEBP" in data[:32]:
        return ".webp"
    return default

def try_download(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=45, allow_redirects=True)
        ctype = r.headers.get("Content-Type","").lower()
        if r.status_code == 200 and "text/html" not in ctype:
            return r.content
    except Exception:
        return None
    return None

def _close_cookie_banner(page):
    selectors = [
        'button:has-text("Accetta")',
        'button:has-text("Accetto")',
        'button:has-text("Accept")',
        '[id*="cookie"] button',
        '.cc-allow',
        'button[aria-label*="Accept"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(300)
                return
        except Exception:
            pass

def _close_bestprice_modal(page):
    selectors = [
        'a.popup_best_prices_close.close',
        'a[data-dismiss="modal"].popup_best_prices_close',
        '.popup_best_prices_close span',
    ]
    closed = False
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(400)
                closed = True
        except Exception:
            pass
    return closed

def _get_color_name_code(page):
    try:
        txt = page.locator(SEL_COLOR_LABEL).first.inner_text(timeout=2500)
        txt = re.sub(r"\s+", " ", txt).strip()
        m = re.search(r"(.+?)\s*\(([^)]+)\)", txt)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return txt, None
    except Exception:
        return None, None

def _click_with_retries(locator, attempts=3):
    last_err = None
    for _ in range(attempts):
        try:
            locator.scroll_into_view_if_needed(timeout=2000)
            locator.click(timeout=3000)
            return True
        except Exception as e:
            last_err = e
            try:
                locator.hover(timeout=1200)
            except Exception:
                pass
            time.sleep(0.4)
    raise last_err if last_err else RuntimeError("click failed")

def _do_login(page, username: str, password: str):
    if not username or not password:
        return False
    try:
        page.goto("https://www.innovativewear.com/login", wait_until="domcontentloaded")
        page.wait_for_selector("input[name='username'], input[name='email']", timeout=10000)
        try:
            page.fill("input[name='username']", username)
        except Exception:
            page.fill("input[name='email']", username)
        page.fill("input[name='password']", password)
        page.click("button[type='submit'], input[type='submit']")
        page.wait_for_load_state("networkidle", timeout=12000)
        # chiudi eventuali banner post-login
        _close_cookie_banner(page)
        for _ in range(3):
            _close_bestprice_modal(page)
            page.wait_for_timeout(200)
        return True
    except Exception:
        return False

def scrape_with_browser(url: str, out_dir: Path, username: str = None, password: str = None):
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1600, "height": 1000},
        )
        page = ctx.new_page()

        # Login (se fornito)
        _do_login(page, username, password)

        # Vai alla pagina prodotto
        page.goto(url, wait_until="domcontentloaded")
        _close_cookie_banner(page)
        for _ in range(3):
            _close_bestprice_modal(page)
            page.wait_for_timeout(150)

        # SKU
        try:
            sku = (page.locator(SEL_SKU).first.text_content() or "").strip().upper()
        except Exception:
            sku = ""
        if not sku:
            sku = url.rstrip("/").split("/")[-1].upper()

        # Assicurati di avere main image & swatches
        try:
            page.wait_for_selector(SEL_MAIN_IMG, timeout=8000)
        except PWTimeout:
            pass
        try:
            page.wait_for_selector(SEL_SWATCHES, timeout=6000)
        except PWTimeout:
            pass

        swatches = page.locator(SEL_SWATCHES)
        count = swatches.count()
        if count == 0:
            count = 1  # fallback: singola immagine

        seen_codes = set()

        # --- ciclo sequenziale: clicca ogni swatch, aspetta aggiornamento, salva ---
        for i in range(count):
            # pulizia overlay ad ogni giro
            for _ in range(2):
                _close_bestprice_modal(page)
                page.wait_for_timeout(150)

            # clic sullo swatch corrente (anche il primo)
            if swatches.count() > 0:
                a = swatches.nth(i)
                try:
                    _click_with_retries(a, attempts=3)
                except Exception:
                    try:
                        page.evaluate("(el)=>el.click()", a)
                    except Exception:
                        pass
                _close_bestprice_modal(page)

            # attesa che label + main image siano disponibili/aggiornate
            try:
                page.wait_for_selector(SEL_COLOR_LABEL, timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_selector(SEL_MAIN_IMG, timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(800)  # piccolo buffer per JS

            # leggi nome/codice colore
            color_name, color_code = _get_color_name_code(page)
            if not color_name:
                color_name = f"Color_{i+1}"
            if not color_code:
                color_code = f"C{i+1}"

            if color_code in seen_codes:
                continue
            seen_codes.add(color_code)

            # prova link HD (non blocca)
            hd_url = None
            try:
                loc = page.locator(SEL_HD).first
                if loc and loc.count() > 0:
                    hd_url = loc.get_attribute("href")
            except Exception:
                hd_url = None

            if hd_url:
                hd_abs = urllib.parse.urljoin(url, hd_url)
                data = try_download(hd_abs)
                if data:
                    ext = guess_ext_from_bytes(data)
                    fname = f"{sku} - {filename_sanitize(color_name)} ({color_code}){ext}"
                    (out_dir / fname).write_bytes(data)
                    results.append({"method": "hd_link", "file": fname, "url": hd_abs, "color": {"name": color_name, "code": color_code}})
                    continue

            # fallback: main image corrente
            try:
                src = page.locator(SEL_MAIN_IMG).first.get_attribute("src")
            except Exception:
                src = None
            if not src:
                results.append({"method": "failed", "reason": "no main image", "color": {"name": color_name, "code": color_code}})
                continue

            src_abs = urllib.parse.urljoin(url, src)
            data = try_download(src_abs)
            if not data:
                results.append({"method": "failed", "reason": "download failed", "url": src_abs, "color": {"name": color_name, "code": color_code}})
                continue

            ext = guess_ext_from_bytes(data)
            fname = f"{sku} - {filename_sanitize(color_name)} ({color_code}){ext}"
            (out_dir / fname).write_bytes(data)
            results.append({"method": "main", "file": fname, "url": src_abs, "color": {"name": color_name, "code": color_code}})

        ctx.close()
        browser.close()

    return {"sku": sku, "results": results}
