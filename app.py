import streamlit as st
from scraper import scrape_product_page, download_all_colors
from pathlib import Path
import zipfile, io, shutil, os, subprocess, sys

# ─────────────────────────────────────────────────────────────
# Funzioni di supporto per gestire Playwright e Chromium
# ─────────────────────────────────────────────────────────────

def ensure_chromium():
    """
    Verifica/installa Chromium per Playwright.
    Su Streamlit Cloud le dipendenze di sistema arrivano da packages.txt,
    quindi qui installiamo solo il browser.
    """
    flag = Path(".playwright_chromium_ready")
    if flag.exists():
        return True
    try:
        st.info("⚙️ Installo Chromium per Playwright…")
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        st.caption(proc.stdout[-1000:] if proc.stdout else "chromium install ok")
        flag.touch()
        st.success("Chromium installato ✅")
        return True
    except Exception as e:
        st.error("Installazione di Chromium fallita.")
        st.code(str(e))
        return False


def get_browser_scraper():
    """
    Importa dinamicamente il modulo browser_scraper.
    """
    try:
        from browser_scraper import scrape_with_browser
        return scrape_with_browser
    except Exception as e:
        st.error(f"Errore durante l'import di browser_scraper: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Configurazione Streamlit
# ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="InnovativeWear • Image Scraper", page_icon="🧵", layout="centered")

st.title("🧵 InnovativeWear • Image Scraper")
st.write("Incolla una o più URL di pagine prodotto InnovativeWear (una per riga).")

default_url = "https://www.innovativewear.com/vendita/by285"
urls_text = st.text_area(
    "URL prodotto",
    value=default_url,
    height=120,
    placeholder="https://www.innovativewear.com/vendita/XXXX"
)

col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    run_btn = st.button("Estrai & Scarica", type="primary")
with col2:
    skip_hd = st.toggle("Salta tentativi HD (più veloce)", value=False,
                        help="Se attivo, evita euristiche pesanti per trovare la massima risoluzione.")
with col3:
    use_browser = st.toggle("Usa browser headless (accurato)", value=True,
                            help="Clicca ogni swatch come un utente reale e scarica l'immagine principale aggiornata.")

# ─────────────────────────────────────────────────────────────
# Logica principale
# ─────────────────────────────────────────────────────────────

if run_btn:
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    if not urls:
        st.error("Inserisci almeno un URL valido.")
        st.stop()

    workdir = Path("download_out")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    all_results = []
    progress = st.progress(0.0, text="Inizio...")

    for i, url in enumerate(urls, start=1):
        with st.status(f"Elaboro: {url}", expanded=False) as status:
            try:
                if use_browser:
                    ok = ensure_chromium()
                    if not ok:
                        st.stop()

                    scrape_with_browser = get_browser_scraper()
                    if scrape_with_browser is None:
                        st.stop()

                    br = scrape_with_browser(url, workdir)
                    folder = workdir / br['sku']
                    folder.mkdir(parents=True, exist_ok=True)

                    # sposta i file "SKU - Colore..." nella sottocartella SKU
                    for f in os.listdir(workdir):
                        if f.startswith(br['sku'] + ' - '):
                            shutil.move(str(workdir / f), str(folder / f))

                    status.update(label=f"Completato: {br['sku']} ({len(br['results'])} immagini)", state="complete")
                    all_results.append(({'sku': br['sku'], 'title': None, 'colors': []}, br['results']))

                else:
                    meta = scrape_product_page(url)
                    status.update(label=f"SKU {meta.get('sku','?')} • {url}", state="running")
                    folder = workdir / meta["sku"]
                    folder.mkdir(parents=True, exist_ok=True)
                    results = download_all_colors(url=url, meta=meta, out_dir=folder, try_hd=not skip_hd)
                    all_results.append((meta, results))
                    status.update(label=f"Completato: {meta['sku']} ({len(results)} immagini)", state="complete")

            except Exception as e:
                st.warning(f"Errore su {url}: {e}")

        progress.progress(i / len(urls), text=f"{i}/{len(urls)} completati")

    # ─────────────────────────────────────────────────────────────
    # Crea archivio ZIP finale
    # ─────────────────────────────────────────────────────────────
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(workdir):
            for f in files:
                full = Path(root) / f
                zf.write(full, arcname=str(full.relative_to(workdir)))
    mem_zip.seek(0)

    st.success("✅ Archivio pronto per il download.")
    st.download_button(
        label="📦 Scarica immagini in ZIP",
        data=mem_zip,
        file_name="innovativewear_images.zip",
        mime="application/zip"
    )

    # ─────────────────────────────────────────────────────────────
    # Report dei risultati
    # ─────────────────────────────────────────────────────────────
    st.subheader("Dettagli estrazione")
    for meta, results in all_results:
        st.markdown(f"**SKU:** {meta['sku']} &nbsp;&nbsp; **Colori trovati:** {len(results)}")
        st.json(results)
