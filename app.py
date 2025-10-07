# app.py (py312 bundle)
import io, os, re, zipfile, sys, platform
from typing import List, Dict
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
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"], check=True)
    except Exception:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    return True

def _open_browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    return pw, browser, context, page

def scrape_innovativewear_all_colors(url: str) -> Dict:
    base = "https://www.innovativewear.com"
    payload = {"url": url, "sku": None, "variants": []}
    ensure_browser()
    from playwright.sync_api import TimeoutError
    pw, browser, context, page = _open_browser()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        sku_el = page.query_selector("h2.prodCode")
        sku = (sku_el.inner_text().strip() if sku_el else "SKU_SCONOSCIUTO")
        payload["sku"] = sku

        swatches = page.query_selector_all(".wrapperSwitchColore a.js_colorswitch")
        if not swatches:
            hd = page.get_attribute("a.js_downloadPhoto", "href")
            if hd:
                payload["variants"].append({"color": "colore", "images": [urljoin(base, hd)]})
            return payload

        items = []
        for a in swatches:
            code = (a.get_attribute("data-color") or "").strip()
            title = (a.get_attribute("title") or "").strip()
            name = title.split("(")[0].strip() if "(" in title else (title or code or "colore")
            items.append({"code": code, "name": name})

        for it in items:
            sel = f'.wrapperSwitchColore a.js_colorswitch[data-color="{it["code"]}"]'
            el = page.query_selector(sel)
            if not el:
                continue
            before = page.get_attribute("a.js_downloadPhoto", "href")
            el.click()
            try:
                page.wait_for_selector("a.js_downloadPhoto", state="attached", timeout=8000)
            except TimeoutError:
                pass
            page.wait_for_timeout(400)
            after = page.get_attribute("a.js_downloadPhoto", "href")
            if not after or after == before:
                page.wait_for_timeout(600)
                after = page.get_attribute("a.js_downloadPhoto", "href")
            if after:
                payload["variants"].append({"color": it["name"], "images": [urljoin(base, after)]})
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
    browser = pw.chromium.launch(headless=True)
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
        st.info("Avvio scraping (browser headless)…")
        progress = st.progress(0.0)
        payloads = []
        for i, u in enumerate(urls, 1):
            payloads.append(scrape_innovativewear_all_colors(u))
            progress.progress(i/len(urls))
        st.success("Raccolta link HD completata. Creo lo ZIP…")
        blob = build_zip_from_payloads(payloads)
        st.download_button("📦 Scarica ZIP", data=blob, file_name="immagini_innovativewear.zip", mime="application/zip")
    except Exception as e:
        st.error("Errore di avvio.")
        st.exception(e)
