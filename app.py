import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

BASE = "https://www.innovativewear.com"

KEYWORDS = {
    "Black": ["black", "nero"],
    "White": ["white", "bianco", "off white", "off-white", "ivory", "natural"],
    "LightGrey": [
        "sport grey", "sport gray",
        "light oxford",
        "grey heather", "gray heather",
        "heather grey", "heather gray",
        "ash",
        "light grey", "light gray",
        "silver",
        "grey", "gray",
    ],
    "Red": ["red", "rosso", "cardinal", "crimson", "scarlet", "cherry", "burgundy"],
    "Navy": ["navy", "dark navy", "midnight", "marine", "deep navy"],
    "Royal": ["royal", "royal blue", "bright blue", "cobalt"],
}

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())

def _clean_color_label(s: str) -> str:
    s = s.strip()
    s = s.split("\n")[0].strip()
    s = re.sub(r"\(\s*[^)]+\s*\)", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _sanitize_filename(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_\-]+", "", s)
    return s

def _score(target: str, name: str) -> int:
    n = _norm(name)
    score = 0
    for i, kw in enumerate(KEYWORDS.get(target, [])):
        if kw in n:
            score += 1000 - i * 10
    # penalità “grigi scuri” quando cerchi grigio chiaro
    if target == "LightGrey" and any(bad in n for bad in ["charcoal", "dark", "graphite", "dark heather"]):
        score -= 250
    return score

def _pick_best_for_target(target: str, available_names: list[str]) -> str | None:
    ranked = sorted((( _score(target, n), n) for n in available_names), reverse=True, key=lambda x: x[0])
    if not ranked:
        return None
    best_score, best_name = ranked[0]
    return best_name if best_score > 0 else None

def _extract_size(url: str) -> tuple[int, int]:
    m = re.search(r"(\d{2,5})x(\d{2,5})", url)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

def _best_img_url(urls: set[str]) -> str | None:
    if not urls:
        return None
    def rank(u: str):
        w, h = _extract_size(u)
        area = w * h
        bonus = 1 if "opt-" in u else 0
        return (area, bonus, len(u))
    return sorted(urls, key=rank, reverse=True)[0]

def scrape_with_browser(url: str, out_dir: Path, username: str = "", password: str = "",
                       targets: list[str] | None = None, try_hd: bool = True):
    """
    Ritorna:
    {
      "sku": "GLSF500",
      "results": [
        {"target": "Black", "color": "Black", "file": "download_out/GLSF500_Black.jpg", "img_url": "..."},
        ...
      ]
    }
    """
    targets = targets or ["Black", "White", "LightGrey", "Red", "Navy", "Royal"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

        # Se serve login: (qui dipende dal sito; se il tuo vecchio scraper lo fa già, riusa quello)
        # Se hai già flusso login funzionante, NON duplicarlo: integra qui.

        # SKU
        sku_el = page.locator("h2.prodCode, .prodCode").first
        sku = _sanitize_filename(sku_el.inner_text().strip())

        # Colori disponibili: titolo del link es. "Black (36)"
        color_links = page.locator("a.js_colorswitch, a.colorSwitch, a[data-color][data-fid1]")
        available = []
        link_map = {}  # name -> nth index
        for i in range(color_links.count()):
            a = color_links.nth(i)
            title = a.get_attribute("title") or ""
            name = title.split("(")[0].strip() if title else ""
            name = _clean_color_label(name)
            if name and name not in link_map:
                link_map[name] = i
                available.append(name)

        results = []

        for target in targets:
            chosen = _pick_best_for_target(target, available)
            if not chosen:
                results.append({"target": target, "color": None, "file": None, "img_url": None, "note": "No match"})
                continue

            # click colore
            color_links.nth(link_map[chosen]).click(force=True)
            page.wait_for_timeout(1200)

            # label colore selezionato (per naming reale)
            label = None
            for sel in ["p.colorLabel.js_searchable", "p.colorLabel", ".colorLabel"]:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    label = _clean_color_label(loc.inner_text())
                    break
            if not label:
                label = chosen
            label_safe = _sanitize_filename(label)

            # trova immagine migliore
            img_urls = set()

            main_img = page.locator("#js_productMainPhoto img, .wrapperFoto img, img.callToZoom").first
            if main_img.count() > 0:
                # url immagine corrente
                src = main_img.get_attribute("src")
                if src:
                    img_urls.add(urljoin(BASE, src))

                if try_hd:
                    # prova ad aprire zoom
                    try:
                        main_img.click(timeout=1500)
                        page.wait_for_timeout(600)
                    except:
                        pass

                    # raccogli possibili HD da modal/DOM
                    for sel in ["#myZoomModal img", ".modal img", "img[src*='opt-']", "a[href*='opt-']"]:
                        loc = page.locator(sel)
                        for j in range(loc.count()):
                            el = loc.nth(j)
                            for attr in ["src", "href"]:
                                u = el.get_attribute(attr)
                                if u and any(ext in u.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                                    img_urls.add(urljoin(BASE, u))

            best = _best_img_url(img_urls)
            if not best:
                results.append({"target": target, "color": label, "file": None, "img_url": None, "note": "No image"})
                continue

            # estensione
            ext = ".jpg"
            mext = re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", best, re.IGNORECASE)
            if mext:
                ext = "." + mext.group(1).lower().replace("jpeg", "jpg")

            filename = f"{sku}_{label_safe}{ext}"
            out_path = out_dir / filename

            # download via Playwright request
            resp = page.request.get(best)
            if not resp.ok:
                results.append({"target": target, "color": label, "file": None, "img_url": best, "note": f"HTTP {resp.status}"})
                continue

            out_path.write_bytes(resp.body())

            results.append({"target": target, "color": label, "file": str(out_path), "img_url": best})

        browser.close()
        return {"sku": sku, "results": results}
