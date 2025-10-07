import streamlit as st
from pathlib import Path
import zipfile, io, shutil, os, subprocess, sys
from scraper import scrape_product_page, download_all_colors

# ───────────────────────────────────────────────
# Supporto Playwright
# ───────────────────────────────────────────────
def ensure_chromium():
    flag = Path(".playwright_chromium_ready")
    if flag.exists():
        return True
    try:
        st.info("⚙️ Installo Chromium per Playwright (può richiedere 1 minuto)...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        flag.touch()
        st.success("Chromium installato ✅")
        return True
    except Exception as e:
        st.error(f"Installazione fallita: {e}")
        return False


def get_browser_scraper():
    try:
        from browser_scraper import scrape_with_browser
        return scrape_with_browser
    except Exception as e:
        st.error(f"Errore durante l'import di browser_scraper: {e}")
        return None


# ───────────────────────────────────────────────
# Interfaccia utente Streamlit
# ───────────────────────────────────────────────
st.set_page_config(page_title="InnovativeWear Image Scraper", page_icon="🧵", layout="centered")
st.title("🧵 InnovativeWear • Image Scraper")

st.markdown(
    "Scarica automaticamente le **immagini HD** per ogni variante colore dei prodotti InnovativeWear."
)

urls_text = st.text_area(
    "URL del prodotto",
    value="https://www.innovativewear.com/vendita/cwu02k",
    height=100,
)

st.subheader("Credenziali di accesso (necessarie per vedere le immagini complete)")
user = st.text_input("Email o username", type="default", value="")
pwd = st.text_input("Password", type="password", value="")

col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    run_btn = st.button("🚀 Estrai e scarica", type="primary")
with col2:
    skip_hd = st.toggle("Salta HD", value=False)
with col3:
    use_browser = st.toggle("Usa browser headless (accurato)", value=True)

# ───────────────────────────────────────────────
# Logica principale
# ───────────────────────────────────────────────
if run_btn:
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    if not urls:
        st.error("Inserisci almeno un URL valido.")
        st.stop()

    workdir = Path("download_out")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    progress = st.progress(0.0)
    all_results = []

    for i, url in enumerate(urls, start=1):
        st.write(f"🔎 Elaboro {url} ...")

        try:
            if use_browser:
                ok = ensure_chromium()
                if not ok:
                    st.stop()

                scrape_with_browser = get_browser_scraper()
                if not scrape_with_browser:
                    st.stop()

                br = scrape_with_browser(url, workdir, username=user, password=pwd)

                folder = workdir / br["sku"]
                folder.mkdir(exist_ok=True)
                for f in os.listdir(workdir):
                    if f.startswith(br["sku"] + " - "):
                        shutil.move(str(workdir / f), str(folder / f))

                st.success(f"✅ {br['sku']}: {len(br['results'])} immagini salvate")
                all_results.append(br)

            else:
                meta = scrape_product_page(url)
                results = download_all_colors(url=url, meta=meta, out_dir=workdir, try_hd=not skip_hd)
                all_results.append({"sku": meta["sku"], "results": results})

        except Exception as e:
            st.error(f"Errore su {url}: {e}")

        progress.progress(i / len(urls))

    # ───────────────────────────────────────────────
    # ZIP finale
    # ───────────────────────────────────────────────
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(workdir):
            for f in files:
                full = Path(root) / f
                zf.write(full, arcname=str(full.relative_to(workdir)))
    mem_zip.seek(0)

    st.download_button(
        "📦 Scarica ZIP delle immagini",
        data=mem_zip,
        file_name="innovativewear_images.zip",
        mime="application/zip",
    )

    st.subheader("📋 Dettagli estrazione")
    for br in all_results:
        st.markdown(f"**SKU:** {br['sku']} ({len(br['results'])} immagini)")
        st.json(br["results"])
