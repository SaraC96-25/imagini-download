import streamlit as st
from scraper import scrape_product_page, download_all_colors
from pathlib import Path
import zipfile, io, shutil, os

st.set_page_config(page_title="InnovativeWear Image Scraper", page_icon="🧵", layout="centered")

st.title("🧵 InnovativeWear • Image Scraper")
st.write("Incolla una o più URL di pagine prodotto InnovativeWear (una per riga).")

default_url = "https://www.innovativewear.com/vendita/by285"
urls_text = st.text_area("URL prodotto", value=default_url, height=120, placeholder="https://www.innovativewear.com/vendita/XXXX")

col1, col2 = st.columns([1,1])
with col1:
    run_btn = st.button("Estrai & Scarica", type="primary")
with col2:
    skip_hd = st.toggle("Salta tentativi HD (più veloce)", value=False, help="Se attivo, evita heuristics pesanti per trovare la massima risoluzione.")

output_zip = None

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
                meta = scrape_product_page(url)
                status.update(label=f"SKU {meta.get('sku','?')} • {url}", state="running")
                folder = workdir / meta["sku"]
                folder.mkdir(parents=True, exist_ok=True)
                results = download_all_colors(url=url, meta=meta, out_dir=folder, try_hd=not skip_hd)
                all_results.append((meta, results))
                status.update(label=f"Completato: {meta['sku']} ({len(results)} immagini)", state="complete")
            except Exception as e:
                st.warning(f"Errore su {url}: {e}")
        progress.progress(i/len(urls), text=f"{i}/{len(urls)} completati")

    # Crea ZIP in memoria
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(workdir):
            for f in files:
                full = Path(root)/f
                zf.write(full, arcname=str(full.relative_to(workdir)))
    mem_zip.seek(0)

    st.success("Archivio pronto.")
    st.download_button(
        label="Scarica immagini in ZIP",
        data=mem_zip,
        file_name="innovativewear_images.zip",
        mime="application/zip"
    )

    st.subheader("Dettagli estrazione")
    for meta, results in all_results:
        st.markdown(f"**SKU:** {meta['sku']} &nbsp;&nbsp; **Titolo:** {meta.get('title') or '-'} &nbsp;&nbsp; **Colori trovati:** {len(meta['colors'])}")
        st.write({k: v for k, v in meta.items() if k not in ('colors','soup')})
        st.json({"colors": meta["colors"]})
        for r in results:
            st.write(r)
