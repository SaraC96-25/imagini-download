# Innovative Wear – Downloader (Python 3.12 bundle)

Questo bundle forza Streamlit Cloud a usare **Python 3.12**, garantendo compatibilità piena con Playwright e Chromium.

## File inclusi
- `app.py` – scraping con Playwright (click swatch → link “Scarica foto in HD”).
- `requirements.txt` – librerie Python.
- `packages.txt` – dipendenze di sistema utili a Chromium.
- `runtime.txt` – impone Python 3.12 su Streamlit Cloud.

## Deploy
1. Carica i file nel repo GitHub (root).
2. Su Streamlit Cloud seleziona il repo e come main module `app.py`.
3. Al primo avvio installerà Chromium; se necessario riavvia l’app.
