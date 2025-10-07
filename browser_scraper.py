# browser_scraper.py
# Versione completa con login e chiusura modale Best Price

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

def enlarge_url_candidates(src_url: str) -> list[str]:
    out = [src_url]
    parsed = urllib.parse.urlparse(src_url)
    path = parsed.path
    parts = path.rsplit("/", 1)
    if len(parts) == 2:
        head, fname = parts
        fname2 = re.sub(r"opt-\d+x\d+-", "", fname)
        if fname2 != fname:
            out.append(urllib.parse.urlunparse(parsed._replace(path=f"{head}/{fname2}")))
    for size in ["80x80", "113x40", "490x735", "600x600", "800x800"]:
        out.append(src_url.replace(f"opt-{size}", "opt-1600x1600"))
        out.append(src_url.replace(f"opt-{size}", "opt-1200x1200"))
        out.append(src_url.replace(f"opt-{size}", "opt-1000x1000"))
    seen = set()
    out2 = []
    for u in out:
        if u not in seen:
            out2.append(u)
            seen.add(u)
    return out2

def try_download(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=45, allow_redirects=True)
        ctype = r.headers.get("Content-Type", "").lower()
        if r.status_code == 200 and "text/html" not in ctype:
            return r.content
    except Exception:
        return None
    return None

def _close_cookie_banner(page):
    selectors = [
        'button:has-text("Accetta")',
        'button:has-text("Accept")',
        '[id*="cookie"] button',
        '.cc-allow',
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
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(500)
                return True
        except Exception:
            pass
    return False

def _get_color_name_code(page):
    try:
        txt = page.locator(SEL_COLOR_LABEL).first.inner_text(timeout=2000)
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
                locator.hover(timeout=1500)
            except Exception:
                pass
            time.sleep(0.4)
    raise last_err if last_err else RuntimeError("click failed")

def _do_login(page, username: str, password: str):
    if not username or not password:
        return False
    try:
        page.goto("https://www.innovativewear.com/login", wait_until="domcontentloaded")
        page.wait_for_selector("input[name='username'], input[name='email']", timeout=8000)
        try:
            page.fill("input[name='username']", username)
        except Exception:
            page.fill("input[name='email']", username)
        page.fill("input[name='password']", password)
        page.click("button[type='submit'], input[type='submit']")
        page.wait_for_load_state("networkidle", timeout=10000)
        if page.locator("a[href*='/logout']").count() > 0:
            return True
        return False
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
        _do_login(page, username, password)
        page.goto(url, wait_until="domcontentloaded")
        _close_cookie_banner(page)
        _close_bestprice_modal(page)
        try:
            sku = (page.locator(SEL_SKU).first.text_content() or "").strip().upper()
        except Exception:
            sku = ""
        if not sku:
            sku = url.rstrip("/").split("/")[-1].upper()
        page.wait_for_selector(SEL_MAIN_IMG, timeout=8000)
        try:
            page.wait_for_selector(SEL_SWATCHES, timeout=5000)
        except PWTimeout:
            pass
        swatches = page.locator(SEL_SWATCHES)
        count = swatches.count()
        if count == 0:
            count = 1
        seen_codes = set()
        for i in range(count):
            _close_bestprice_modal(page)
            color_name, color_code = _get_color_name_code(page)
            old_src = page.locator(SEL_MAIN_IMG).first.get_attribute("src")
            if swatches.count() > 0:
                a = swatches.nth(i)
                try:
                    _click_with_retries(a)
                except Exception:
                    try:
                        page.evaluate("(el)=>el.click()", a)
                    except Exception:
                        pass
                _close_bestprice_modal(page)
                time.sleep(1)
            n_name, n_code = _get_color_name_code(page)
            color_name = n_name or color_name or "Color"
            color_code = n_code or color_code
            if color_code and color_code in seen_codes:
                continue
            if color_code:
                seen_codes.add(color_code)
            src = page.locator(SEL_MAIN_IMG).first.get_attribute("src")
            if src:
                src_abs = urllib.parse.urljoin(url, src)
                data = try_download(src_abs)
                if data:
                    ext = guess_ext_from_bytes(data)
                    fname = f"{sku} - {filename_sanitize(color_name)} ({color_code}){ext}"
                    (out_dir / fname).write_bytes(data)
                    results.append({"file": fname, "url": src_abs, "color": color_code})
        ctx.close()
        browser.close()
    return {"sku": sku, "results": results}
