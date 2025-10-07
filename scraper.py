from bs4 import BeautifulSoup
import requests, re, os, urllib.parse, mimetypes, time
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

# CSS selectors (aggiorna qui se il sito cambia)
SEL_SKU = "h2.prodCode, h2.prodcode, h2 .prodCode"
SEL_TITLE = "h1.productTitle, h1, .product-title"
SEL_COLOR_LABEL = "p.colorLabel.js_searchable, p.colorLabel, .colorLabel"
SEL_MAIN_IMG = "#js_productMainPhoto img, .wrapperFoto img"
SEL_THUMBS_IMG = "#js_productThumbs img.js_productThumb, .wrapperThumbs img"
SEL_HD_LINK = "a.js_downloadPhoto[href*='product_photo_download']"
SEL_SWATCH = "#js_availablecolorsheader .wrapperSwitchColore a.js_colorswitch, a.colorSwitch"

DOWNLOAD_RE = re.compile(r"""href\s*=\s*["'](?P<url>/product_photo_download\?[^"']+)["']""", re.I)
ID_IN_QUERY_RE = re.compile(r"[?&](?:id|fid1|file_id)=(\d+)")

def _session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.headers.update(HEADERS)
    return s

def _get(session: requests.Session, url: str):
    resp = session.get(url, timeout=30, allow_redirects=True)
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
    sku = "SKU"
    sku_el = soup.select_one(SEL_SKU)
    if sku_el:
        sku = sku_el.get_text(strip=True)
    else:
        # fallback: deduci da URL (ultima parte)
        slug = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
        sku = slug.upper()
    sku = filename_sanitize(sku)

    # Title (best effort)
    title = None
    t_el = soup.select_one(SEL_TITLE)
    if t_el:
        title = t_el.get_text(strip=True)

    # colore corrente (label)
    current_color = None
    color_label_el = soup.select_one(SEL_COLOR_LABEL)
    if color_label_el:
        # Esempio: "Black (BLK)"
        txt = color_label_el.get_text(separator=" ", strip=True)
        m = re.search(r"(.+?)\s*\(([^)]+)\)", txt)
        if m:
            current_color = {"name": m.group(1).strip(), "code": m.group(2).strip()}
        else:
            current_color = {"name": txt.strip(), "code": None}

    # swatches
    colors = []
    for a in soup.select(SEL_SWATCH):
        title_attr = a.get("title") or ""
        txt = title_attr.strip() or a.get_text(strip=True)
        name, code = None, None
        m = re.search(r"(.+?)\s*\(([^)]+)\)", txt)
        if m:
            name, code = m.group(1).strip(), m.group(2).strip()
        else:
            name = txt
            code = a.get("data-color") or None
        fid1 = a.get("data-fid1") or None
        colors.append({"name": name, "code": code, "fid1": fid1})

    # main image
    main_img = None
    img_el = soup.select_one(SEL_MAIN_IMG)
    if img_el and img_el.get("src"):
        main_img = absolute(url, img_el["src"])

    # thumbs
    thumbs = []
    for t in soup.select(SEL_THUMBS_IMG):
        src = t.get("src")
        if src:
            thumbs.append(absolute(url, src))

    # link HD (per colore selezionato attuale)
    hd_link = None
    hd_el = soup.select_one(SEL_HD_LINK)
    if hd_el and hd_el.get("href"):
        hd_link = absolute(url, hd_el["href"])

    # tutti i possibili link di download presenti nell'HTML
    all_hd_links = []
    for m in DOWNLOAD_RE.finditer(html):
        all_hd_links.append(absolute(url, m.group("url")))
    all_hd_links = list(dict.fromkeys(all_hd_links))

    # mappa id->link
    id_to_link = {}
    for l in all_hd_links:
        m = ID_IN_QUERY_RE.search(l)
        if m:
            id_to_link[m.group(1)] = l

    return {
        "url": url,
        "sku": sku,
        "title": title,
        "current_color": current_color,
        "colors": colors,
        "main_img": main_img,
        "thumbs": thumbs,
        "hd_link": hd_link,
        "all_hd_links": all_hd_links,
        "id_to_link": id_to_link,
        "raw_html_len": len(html),
    }, soup

def try_download(session: requests.Session, url: str) -> bytes | None:
    try:
        r = session.get(url, timeout=45, allow_redirects=True)
        ctype = r.headers.get("Content-Type","").lower()
        if r.status_code == 200 and "text/html" not in ctype:
            return r.content
    except Exception:
        return None
    return None

def enlarge_url_candidates(src_url: str) -> list[str]:
    out = [src_url]
    parsed = urllib.parse.urlparse(src_url)
    path = parsed.path
    parts = path.rsplit("/", 1)
    if len(parts) == 2:
        head, fname = parts
        # rimuovi "opt-WxH-" dal filename
        fname2 = re.sub(r"opt-\d+x\d+-", "", fname)
        if fname2 != fname:
            out.append(urllib.parse.urlunparse(parsed._replace(path=f"{head}/{fname2}")))
    for size in ["80x80", "113x40", "490x735", "600x600", "800x800"]:
        out.append(src_url.replace(f"opt-{size}", "opt-1600x1600"))
        out.append(src_url.replace(f"opt-{size}", "opt-1200x1200"))
        out.append(src_url.replace(f"opt-{size}", "opt-1000x1000"))
    return list(dict.fromkeys(out))

def guess_ext_from_bytes(data: bytes, default=".jpg") -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and b"WEBP" in data[:32]:
        return ".webp"
    return default

def download_all_colors(url: str, meta: dict, out_dir: Path, try_hd: bool = True):
    session = _session()
    _ = session.get(url)  # warm cookies

    saved = []

    def save_image_bytes(data: bytes, base_name: str):
        ext = guess_ext_from_bytes(data)
        fname = f"{filename_sanitize(base_name)}{ext}"
        path = out_dir / fname
        with open(path, "wb") as f:
            f.write(data)
        return str(path.name), len(data)

    # 1) Scarica tutti i link di download HD noti nel sorgente
    #    Cerca di mapparli ai colori usando fid1/id
    id_to_link = meta.get("id_to_link", {})
    colors = meta.get("colors") or []
    code_to_color = {}
    fid1_to_color = {}
    for c in colors:
        if c.get("code"):
            code_to_color[c["code"]] = c
        if c.get("fid1"):
            fid1_to_color[str(c["fid1"])] = c

    used_ids = set()
    for fid, link in id_to_link.items():
        data = try_download(session, link) if try_hd else None
        if data:
            used_ids.add(fid)
            color = fid1_to_color.get(str(fid))
            # Nome file: SKU - Nome (COD) oppure SKU - download-{fid}
            if color:
                base = f"{meta['sku']} - {color.get('name','Color')}"
                if color.get('code'):
                    base += f" ({color['code']})"
            else:
                base = f"{meta['sku']} - download-{fid}"
            fname, size = save_image_bytes(data, base)
            saved.append({"method": "product_photo_download", "file": fname, "bytes": size, "url": link, "color": color})

    # 2) Per colori senza file, prova fallback: main image + thumbs (ingrandite)
    remaining = []
    if colors:
        for c in colors:
            # Se non abbiamo già un file con questo codice colore
            code = c.get("code")
            already = any((rec.get("color") or {}).get("code")==code for rec in saved if rec.get("color"))
            if not already:
                remaining.append(c)
    else:
        # se non ci sono colori, trattiamo un singolo "default"
        remaining = [{"name": meta.get("current_color",{}).get("name") or "Default", "code": meta.get("current_color",{}).get("code")}]

    # Candidati immagine dalla pagina
    candidates = []
    if meta.get("main_img"):
        candidates.extend(enlarge_url_candidates(meta["main_img"]) if try_hd else [meta["main_img"]])
    for t in meta.get("thumbs", []):
        candidates.extend(enlarge_url_candidates(t) if try_hd else [t])
    # deduplicate mantenendo ordine
    seen = set(); candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    for c in remaining:
        best = None; best_src = None
        for cand in candidates:
            data = try_download(session, cand)
            if data and (best is None or len(data) > len(best)):
                best, best_src = data, cand
        if best:
            base = f"{meta['sku']} - {c.get('name') or 'Color'}"
            if c.get("code"):
                base += f" ({c['code']})"
            fname, size = save_image_bytes(best, base)
            saved.append({"method": "main/thumbs_fallback", "file": fname, "bytes": size, "url": best_src, "color": c})
        else:
            saved.append({"method": "failed", "color": c, "reason": "no image candidates downloadable"})

    # 3) Se ancora nulla salvato, tenta almeno la main image se esiste
    if not saved and meta.get("main_img"):
        data = try_download(session, meta["main_img"])
        if data:
            base = f"{meta['sku']} - default"
            fname, size = save_image_bytes(data, base)
            saved.append({"method": "last_resort_main", "file": fname, "bytes": size, "url": meta["main_img"], "color": None})

    return saved

def scrape_product_page(url: str) -> dict:
    with _session() as s:
        meta, _soup = parse_page(s, url)
        return meta
