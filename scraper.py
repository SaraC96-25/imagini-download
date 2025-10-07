from bs4 import BeautifulSoup
import requests, re, os, urllib.parse, mimetypes
from pathlib import Path

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# CSS selectors (aggiorna qui se il sito cambia)
SEL_SKU = "h2.prodCode"
SEL_TITLE = "h1.productTitle, h1, .product-title"
SEL_COLOR_LABEL = "p.colorLabel.js_searchable"
SEL_MAIN_IMG = "#js_productMainPhoto img"
SEL_HD_LINK = "a.js_downloadPhoto[href*='product_photo_download']"
SEL_SWATCH = "#js_availablecolorsheader .wrapperSwitchColore a.js_colorswitch"

def _get(session: requests.Session, url: str):
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp

def absolute(base_url: str, maybe_path: str) -> str:
    return urllib.parse.urljoin(base_url, maybe_path)

def filename_sanitize(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name

def parse_page(session: requests.Session, url: str):
    html = _get(session, url).text
    soup = BeautifulSoup(html, "lxml")

    # SKU
    sku_el = soup.select_one(SEL_SKU)
    sku = sku_el.get_text(strip=True) if sku_el else "SKU"
    sku = filename_sanitize(sku)

    # Title (best effort)
    title = None
    t_el = soup.select_one(SEL_TITLE)
    if t_el:
        title = t_el.get_text(strip=True)

    # colore corrente (label)
    color_label_el = soup.select_one(SEL_COLOR_LABEL)
    current_color = None
    if color_label_el:
        # Esempio: "Black (BLK)"
        txt = color_label_el.get_text(separator=" ", strip=True)
        # estrai Nome e (COD)
        m = re.search(r"(.+?)\s*\(([^)]+)\)", txt)
        if m:
            current_color = {"name": m.group(1).strip(), "code": m.group(2).strip()}
        else:
            current_color = {"name": txt.strip(), "code": None}

    # swatches
    colors = []
    for a in soup.select(SEL_SWATCH):
        title_attr = a.get("title") or ""
        # es: "Antracite Melange (6MA)"
        m = re.search(r"(.+?)\s*\(([^)]+)\)", title_attr)
        if m:
            name, code = m.group(1).strip(), m.group(2).strip()
        else:
            name, code = title_attr.strip() or a.get_text(strip=True), a.get("data-color") or None
        fid1 = a.get("data-fid1") or None
        colors.append({"name": name, "code": code, "fid1": fid1})

    # main image
    main_img = None
    img_el = soup.select_one(SEL_MAIN_IMG)
    if img_el and img_el.get("src"):
        main_img = absolute(url, img_el["src"])

    # link HD (per colore selezionato attuale)
    hd_link = None
    hd_el = soup.select_one(SEL_HD_LINK)
    if hd_el and hd_el.get("href"):
        hd_link = absolute(url, hd_el["href"])

    return {
        "url": url,
        "sku": sku,
        "title": title,
        "current_color": current_color,
        "colors": colors,
        "main_img": main_img,
        "hd_link": hd_link,
        "raw_html_len": len(html),
    }, soup

def try_download(session: requests.Session, url: str) -> bytes | None:
    try:
        r = session.get(url, headers=HEADERS, timeout=45, allow_redirects=True)
        if r.status_code == 200 and "text/html" not in r.headers.get("Content-Type","").lower():
            return r.content
    except Exception:
        return None
    return None

def enlarge_url_candidates(src_url: str) -> list[str]:
    """
    Tenta varianti rimuovendo prefissi di resize (es. opt-490x735-) o cartelle intermedie.
    """
    out = [src_url]
    # 1) prova a rimuovere sequenze 'opt-###x###-' dal filename
    parsed = urllib.parse.urlparse(src_url)
    path = parsed.path
    # filtra filename
    parts = path.rsplit("/", 1)
    if len(parts) == 2:
        head, fname = parts
        fname2 = re.sub(r"opt-\d+x\d+-", "", fname)
        if fname2 != fname:
            out.append(urllib.parse.urlunparse(parsed._replace(path=f"{head}/{fname2}")))
    # 2) prova a sostituire 'opt-80x80' / 'opt-490x735' con 'opt-1000x1000'
    for size in ["80x80", "113x40", "490x735", "600x600", "800x800"]:
        out.append(src_url.replace(f"opt-{size}", "opt-1600x1600"))
        out.append(src_url.replace(f"opt-{size}", "opt-1200x1200"))
        out.append(src_url.replace(f"opt-{size}", "opt-1000x1000"))
    return list(dict.fromkeys(out))

def guess_ext_from_bytes(data: bytes, default=".jpg") -> str:
    # molto semplice: usa header
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and b"WEBP" in data[:32]:
        return ".webp"
    return default

def download_all_colors(url: str, meta: dict, out_dir: Path, try_hd: bool = True):
    """
    Prova vari approcci:
    - Se presente hd_link, usa quello per il colore corrente.
    - Per ogni swatch: prova combinazioni plausibili:
        1) /product_photo_download?id={fid1} (se fid1 esiste)
        2) /product_photo_download?fid1={fid1}
        3) /product_photo_download?color={code}
        4) Variante della main_img ingrandita
    Salva il file migliore trovato.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Ricarica pagina per cookie
    _ = session.get(url, headers=HEADERS, timeout=30)

    saved = []

    # Helper salvataggio
    def save_image_bytes(data: bytes, color_name: str, color_code: str | None):
        ext = guess_ext_from_bytes(data)
        fname = f"{meta['sku']} - {filename_sanitize(color_name)}"
        if color_code:
            fname += f" ({filename_sanitize(color_code)})"
        fname += ext
        path = out_dir / fname
        with open(path, "wb") as f:
            f.write(data)
        return str(path.name), len(data)

    # Selezioni colore
    colors = meta.get("colors") or []
    if not colors and meta.get("current_color"):
        colors = [meta["current_color"]]

    # Per il colore corrente: prova link HD diretto se disponibile
    if try_hd and meta.get("hd_link") and meta.get("current_color"):
        data = try_download(session, meta["hd_link"])
        if data:
            name = save_image_bytes(data, meta["current_color"]["name"], meta["current_color"]["code"])
            saved.append({"color": meta["current_color"], "method": "hd_link", "file": name})
        # prosegui comunque per altri colori

    # Loop swatches
    for c in colors:
        color_name = c.get("name") or "Color"
        color_code = c.get("code")
        fid1 = c.get("fid1")

        # Evita doppioni se già salvato da hd_link con stesso codice
        if any((rec.get("color",{}).get("code")==color_code and rec["method"]=="hd_link") for rec in saved):
            continue

        tried_urls = []

        if try_hd and fid1:
            # tentative endpoints
            for pattern in [
                "/product_photo_download?id={fid1}",
                "/product_photo_download?fid1={fid1}",
                "/product_photo_download?file_id={fid1}",
            ]:
                u = urllib.parse.urljoin(url, pattern.format(fid1=fid1))
                tried_urls.append(u)
                data = try_download(session, u)
                if data:
                    fname, size = save_image_bytes(data, color_name, color_code)
                    saved.append({"color": c, "method": "download_by_fid1", "file": fname, "bytes": size, "url": u})
                    break
            else:
                # fallback a variante su main_img
                pass

        # Se non abbiamo salvato nulla per questo colore, prova main image e varianti
        if not any(rec.get("color",{}).get("code")==color_code for rec in saved):
            # scarica main img (potrebbe essere di un altro colore, ma spesso condivisa)
            main_img = meta.get("main_img")
            data_best = None
            src_used = None
            if main_img:
                for cand in ([main_img] + (enlarge_url_candidates(main_img) if try_hd else [])):
                    if cand in tried_urls:
                        continue
                    tried_urls.append(cand)
                    data = try_download(session, cand)
                    if data and (data_best is None or len(data) > len(data_best)):
                        data_best = data
                        src_used = cand
            if data_best:
                fname, size = save_image_bytes(data_best, color_name, color_code)
                saved.append({"color": c, "method": "main_or_enlarged", "file": fname, "bytes": size, "url": src_used})
            else:
                saved.append({"color": c, "method": "failed", "reason": "no image found", "tried": tried_urls})

    return saved

def scrape_product_page(url: str) -> dict:
    with requests.Session() as s:
        meta, _soup = parse_page(s, url)
        return meta
