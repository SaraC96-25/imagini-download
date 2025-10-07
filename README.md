# InnovativeWear Image Scraper (Streamlit)

App per velocizzare il download di foto prodotto dal sito **innovativewear.com**.
- Inserisci una o più URL di pagine prodotto (una per riga).
- L'app prova a:
  1) Leggere lo **SKU** (`h2.prodCode`),
  2) Estrarre la **lista colori** dagli **swatch**,
  3) Per ogni colore tenta il download della **foto in massima risoluzione** disponibile,
  4) Salva i file in cartelle nominate con **SKU**, con filename = **NomeColore (CODICE).estensione**.

> **Nota importante**
> Il sito è dinamico: lo switch colore avviene via JavaScript. Senza un browser headless, alcune foto HD potrebbero non essere direttamente raggiungibili.
> Ho implementato più **strategie/heuristics**:
> - Usa il link “Scarica foto in HD” se disponibile (e prova alternative per ID/parametri).
> - Se non funziona, scarica la **foto principale** e tenta le varianti di URL più grandi (es. rimuovendo prefissi `opt-490x735-`, ecc.).
> - Come estrema risorsa, salva l'immagine massima trovata tra main e thumb per il colore corrente.
>
> Se in futuro servisse affidabilità al 100% sulle varianti, possiamo integrare **Playwright** per un vero cambio colore con JS, ma Streamlit Cloud richiede build più pesanti.

## Esecuzione locale
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy su Streamlit Cloud
1. Carica questo repository su GitHub.
2. Crea un'app su Streamlit Cloud puntando a `app.py`.
3. Imposta Python 3.9–3.12 e le dipendenze del `requirements.txt`.
4. (Opzionale) Aumenta il `server.maxUploadSize` o usa zip split se gestisci molti prodotti.

## Limitazioni note
- Se il sito cambia markup o endpoint, aggiorna i selettori in `scraper.py` (costanti in cima al file).
- Alcuni endpoint potrebbero richiedere sessione/cookie. Lo script gestisce i cookie base, ma non esegue login.
- Il tool non aggira protezioni anti-scraping. Usalo nel rispetto dei termini del fornitore.


### Troubleshooting
- Se ottieni 0 immagini: prova a disattivare il toggle 'Salta tentativi HD' e ripeti.
- Verifica che l'URL sia una pagina **prodotto** e non una categoria.
- Alcuni prodotti potrebbero non avere swatch o link di download nel sorgente: il tool allora usa main/thumbs.
