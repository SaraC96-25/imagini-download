# app.py (py312 patch2 – varianti & HD robusti)
import io, os, re, zipfile, sys, time
from typing import List, Dict, Optional
from urllib.parse import urljoin
import streamlit as st

st.set_page_config(page_title="Innovative Wear – Downloader", page_icon="🧵", layout="centered")

def sanitize(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text

@st.cache_resource(show_spinner=False)
def ensure_browser():
    import subprocess, sys
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright>=1.50", "greenlet>=3.1"], check=True)
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except Exception:
        pass
    return True

def _open_browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    context = browser.new_context(accept_downloads=True, viewport={"width":1280,"height":1400})
    page = context.new_page()
    return pw, browser, context, page

def _dismiss_banners(page):
    for sel in [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accetta')",
        "button:has-text('Accept')",
        ".cc-allow", ".cc-dismiss", ".cookie-accept",
    ]:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_timeout(200)
        except Exception:
            pass

def _abs(base: str, href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    return urljoin(base, href)

def _largest_from_srcset(srcset: str) -> Optional[str]:
    try:
        parts = [p.strip() for p in srcset.split(",") if p.strip()]
        best = None
        best_w = -1
        for p in parts:
            seg = p.split()
            if not seg:
                continue
            url = seg[0]
            w = 0
            if len(seg) > 1 and seg[1].endswith("w"):
                try:
                    w = int(seg[1][:-1])
                except:
                    w = 0
            if w >= best_w:
                best_w = w
                best = url
        return best
    except Exception:
        return None

def _current_hd_url(page, base: str) -> Optional[str]:
    a = page.query_selector("a.js_downloadPhoto")
    if a:
        href = a.get_attribute("href")
        if href:
            return _abs(base, href)
    img = page.query_selector("#js_productMainPhoto img.callToZoom, #js_productMainPhoto img, img.callToZoom")
    if img:
        srcset = img.get_attribute("srcset")
        if srcset:
            hi = _largest_from_srcset(srcset)
            if hi:
                return _abs(base, hi)
        src = img.get_attribute("src")
        if src:
            return _abs(base, src)
    return None

def _read_sku(page) -> str:
    el = page.query_selector("h2.prodCode")
    return (el.inner_text().strip() if el else "SKU_SCONOSCIUTO")

def _collect_swatch_meta(page) -> List[Dict]:
    anchors = page.query_selector_all("a.js_colorswitch, .js_colorswitch")
    seen = set()
    items = []
    for a in anchors:
        code = (a.get_attribute("data-color") or "").strip()
        title = (a.get_attribute("title") or "").strip()
        name = title.split("(")[0].strip() if "(" in title else (title or code or "colore")
        if not code:
            code = (a.get_attribute("data-fid1") or a.get_attribute("data-product") or "").strip()
        if code and code not in seen:
            seen.add(code)
            items.append({"code": code, "name": name})
    return items

def scrape_innovativewear_all_colors(url: str) -> Dict:
    base = "https://www.innovativewear.com"
    payload = {"url": url, "sku": None, "variants": []}
    ensure_browser()
    pw, browser, context, page = _open_browser()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        _dismiss_banners(page)
        page.wait_for_timeout(600)

        sku = _read_sku(page)
        payload["sku"] = sku

        items = _collect_swatch_meta(page)

        if not items:
            href = _current_hd_url(page, base)
            if href:
                payload["variants"].append({"color": "colore", "images": [href]})
            return payload

        for it in items:
            before = _current_hd_url(page, base)
            sel = f'a.js_colorswitch[data-color="{it["code"]}"], .js_colorswitch[data-color="{it["code"]}"]'
            el = page.query_selector(sel)
            if not el:
                # fallback: clicca il primo matching per titolo
                el = page.query_selector(f'a.js_colorswitch[title*="{it["name"]}"], .js_colorswitch[title*="{it["name"]}"]')
            if not el:
                continue
            el.click()
            t0 = time.time()
            got = None
            while time.time() - t0 < 8.0:
                page.wait_for_timeout(250)
                href = _current_hd_url(page, base)
                if href and href != before:
                    got = href
                    break
            if not got:
                got = _current_hd_url(page, base)
            if got:
                payload["variants"].append({"color": it["name"], "images": [got]})
        # dedup
        uniq = []
        seen = set()
        for v in payload["variants"]:
            key = (v["color"], tuple(v["images"]))
            if key in seen: continue
            seen.add(key); uniq.append(v)
        payload["variants"] = uniq

        return payload
    finally:
        try:
            context.close(); browser.close(); pw.stop()
        except Exception:
            pass

def build_zip_from_payloads(payloads: List[Dict]) -> bytes:
    ensure_browser()
    from playwright.sync_api import sync_playwright
    mem = io.BytesIO()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    try:
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in payloads:
                sku = sanitize(item.get("sku") or "SKU")
                for variant in item.get("variants", []):
                    color = sanitize(variant.get("color") or "colore")
                    urls = variant.get("images", [])
                    multi = len(urls) > 1
                    for idx, img_url in enumerate(urls, 1):
                        try:
                            page.goto("about:blank")
                            with page.expect_download() as dl_info:
                                page.goto(img_url)
                            d = dl_info.value
                            data = d.content()
                            fname = f"{color}_{idx:02d}.jpg" if multi else f"{color}.jpg"
                            zf.writestr(os.path.join(sku, fname), data)
                        except Exception as e:
                            zf.writestr(os.path.join(sku, f"ERROR_{color}_{idx:02d}.txt"), f"{img_url}\n{repr(e)}")
        mem.seek(0)
        return mem.read()
    finally:
        try:
            context.close(); browser.close(); pw.stop()
        except Exception:
            pass

# ------------------------ UI ------------------------
st.title("🧵 Innovative Wear – Downloader immagini per colore")

with st.expander("① Incolla gli URL (uno per riga)"):
    urls_text = st.text_area("URL prodotto Innovative Wear", height=150, placeholder="https://www.innovativewear.com/vendita/by285")
    urls = [u.strip() for u in urls_text.strip().splitlines() if u.strip().startswith("http")] if urls_text.strip() else []

col1, col2 = st.columns([1,1])
with col1:
    start = st.button("🚀 Scarica immagini e crea ZIP", type="primary", disabled=not urls)
with col2:
    st.caption(f"{len(urls)} URL pronti" if urls else "Nessun URL")

if start:
    try:
        st.info("Avvio scraping…")
        progress = st.progress(0.0)
        payloads = []
        for i, u in enumerate(urls, 1):
            payloads.append(scrape_innovativewear_all_colors(u))
            progress.progress(i/len(urls))
        if not payloads:
            st.warning("Nessun dato trovato.")
        else:
            tot_variants = sum(len(p['variants']) for p in payloads)
            st.success(f"Raccolta completata: {len(payloads)} prodotti, {tot_variants} varianti totali. Creo lo ZIP…")
            blob = build_zip_from_payloads(payloads)
            st.download_button("📦 Scarica ZIP", data=blob, file_name="immagini_innovativewear.zip", mime="application/zip")
            for p in payloads:
                tot = sum(len(v['images']) for v in p['variants'])
                st.write(f"- **{p['sku']}**: {len(p['variants'])} varianti, {tot} immagini")
    except Exception as e:
        st.error("Errore di avvio.")
        st.exception(e)
