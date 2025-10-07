from pathlib import Path
import re, urllib.parse
from playwright.sync_api import sync_playwright
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

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

def scrape_with_browser(url: str, out_dir: Path):
    """
    Usa Playwright per:
    - leggere SKU da h2.prodCode
    - cliccare ogni swatch a.js_colorswitch
    - dopo ogni click, aspettare che cambi la main image (#js_productMainPhoto img)
    - provare prima il link “Scarica foto in HD”, altrimenti scaricare la main image
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")

        # SKU
        sku = (page.locator("h2.prodCode").first.text_content() or "").strip().upper()
        if not sku:
            sku = url.rstrip("/").split("/")[-1].upper()

        # swatches
        swatches = page.locator("#js_availablecolorsheader .wrapperSwitchColore a.js_colorswitch")
        count = swatches.count()
        if count == 0:
            count = 1  # fallback: nessuno swatch → singola immagine

        for i in range(count):
            color_name, color_code = None, None

            if swatches.count() > 0:
                a = swatches.nth(i)
                title = (a.get_attribute("title") or "").strip()
                m = re.search(r"(.+?)\s*\(([^)]+)\)", title)
                if m:
                    color_name, color_code = m.group(1).strip(), m.group(2).strip()
                else:
                    color_name = title or (a.text_content() or "Color").strip()
                    color_code = a.get_attribute("data-color") or None

                old_src = page.locator("#js_productMainPhoto img").first.get_attribute("src")
                a.click()
                try:
                    page.wait_for_function(
                        "(oldSrc) => { const img=document.querySelector('#js_productMainPhoto img'); return img && img.getAttribute('src')!==oldSrc; }",
                        arg=old_src, timeout=5000
                    )
                except Exception:
                    page.wait_for_timeout(500)

            # prova link HD
            hd_href = page.locator("a.js_downloadPhoto[href*='product_photo_download']").first.get_attribute("href")
            if hd_href:
                hd_url = urllib.parse.urljoin(url, hd_href)
                data = try_download(hd_url)
                if data:
                    ext = guess_ext_from_bytes(data)
                    base = f"{sku} - {filename_sanitize(color_name or 'Color')}"
                    if color_code:
                        base += f" ({filename_sanitize(color_code)})"
                    fname = f"{base}{ext}"
                    (out_dir / fname).write_bytes(data)
                    results.append({"method": "hd_link", "file": fname, "url": hd_url})
                    continue

            # altrimenti scarica main image
            src = page.locator("#js_productMainPhoto img").first.get_attribute("src")
            if src:
                src_abs = urllib.parse.urljoin(url, src)
                best, used = None, None
                for cand in enlarge_url_candidates(src_abs):
                    data = try_download(cand)
                    if data and (best is None or len(data) > len(best)):
                        best, used = data, cand
                if best:
                    ext = guess_ext_from_bytes(best)
                    base = f"{sku} - {filename_sanitize(color_name or 'Color')}"
                    if color_code:
                        base += f" ({filename_sanitize(color_code)})"
                    fname = f"{base}{ext}"
                    (out_dir / fname).write_bytes(best)
                    results.append({"method": "main_or_enlarged", "file": fname, "url": used})
                else:
                    results.append({"method": "failed", "reason": "no image", "color": color_code})
            else:
                results.append({"method": "failed", "reason": "no main image tag", "color": color_code})

        ctx.close()
        browser.close()

    return {"sku": sku, "results": results}
