"""Scraper iDNES Reality (reality.idnes.cz).

iDNES Reality je server-rendered HTML (žádné JSON API) — parsujeme přes BeautifulSoup.
Seznam výsledků má karty .c-products__item; balkon/terasu/lodžii a popis obsahuje
až detail bytu, takže detail stahujeme jen pro byty, které projdou levným předfiltrem.

Venkovní prostor (hlavní priorita) čteme ze dvou zdrojů zároveň:
  1) strukturovaná tabulka parametrů (řádek "Balkon"/"Terasa"/"Lodžie" = prvek existuje),
  2) popis s klíčovými slovy a ošetřením negace ("bez balkonu").
Ani jeden zdroj sám o sobě nestačí — tabulka občas prvek vynechá a popis zas ne vždy.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import Config
from ..http import Http
from ..models import Listing, normalize_disposition, parse_area
from ..scoring import cheap_prefilter
from ..store import Store
from .base import Scraper, slugify

log = logging.getLogger("hlidac.idnes")

ORIGIN = "https://reality.idnes.cz"
# Preferujeme "okres-…" (pokryje i okolní obce), fallback na variantu bez okresu.
SEARCH_URLS = [
    ORIGIN + "/s/pronajem/byty/okres-{city}/",
    ORIGIN + "/s/pronajem/byty/{city}/",
]

# ID inzerátu v URL detailu je hex ObjectId (24 znaků), např. .../6979f2a795b41d081b029c17/
_ID_RE = re.compile(r"/detail/[^?#]*?/([0-9a-f]{16,})/?", re.I)


class IdnesScraper(Scraper):
    name = "idnes"

    def fetch(self, cfg: Config, http: Http, store: Store) -> list[Listing]:
        city_seo = slugify(cfg.search.mesto)
        listings: list[Listing] = []
        seen_ids: set[str] = set()

        base: str | None = None
        for page in range(1, cfg.max_stran_na_zdroj + 1):
            if page == 1:
                base, html = self._first_page(http, city_seo)
                if not base:
                    log.warning("iDNES: pro '%s' nenalezena funkční URL", cfg.search.mesto)
                    break
            else:
                try:
                    html = http.get(base, params={"page": page}).text
                except Exception as e:
                    log.warning("iDNES: chyba stránky %s: %s", page, e)
                    break

            cards = self._cards(html)
            if not cards:
                break
            added = 0
            for card in cards:
                l = self._parse_list_item(card, cfg)
                if l and l.source_id not in seen_ids:
                    seen_ids.add(l.source_id)
                    listings.append(l)
                    added += 1
            if added == 0:  # stránka bez nových inzerátů -> konec stránkování
                break

        # detail stahujeme jen pro kandidáty, kteří projdou levným předfiltrem (cena/dispozice)
        candidates = [l for l in listings if cheap_prefilter(l, cfg)]
        log.info("iDNES: %d inzerátů, %d kandidátů na detail", len(listings), len(candidates))
        for l in candidates:
            self._enrich_detail(l, cfg, http, store)

        return candidates

    # --- výběr funkční varianty URL --------------------------------------

    def _first_page(self, http: Http, city_seo: str) -> tuple[str | None, str]:
        """Zkusí okres-variantu i variantu bez okresu; vrátí (base_url, html) té, co má karty."""
        for tmpl in SEARCH_URLS:
            url = tmpl.format(city=city_seo)
            try:
                html = http.get(url).text
            except Exception as e:
                log.debug("iDNES: %s nedostupné: %s", url, e)
                continue
            if self._cards(html):
                return url, html
        return None, ""

    @staticmethod
    def _cards(html: str) -> list:
        """Karty inzerátů na stránce seznamu (bez reklamních vsuvek)."""
        soup = BeautifulSoup(html, "lxml")
        return [
            c for c in soup.select(".c-products__item")
            if "c-products__item-advertisment" not in (c.get("class") or [])
        ]

    # --- parsování seznamu ------------------------------------------------

    def _parse_list_item(self, card, cfg: Config) -> Listing | None:
        a = card.select_one("a.c-products__link")
        if not a or not a.get("href"):
            return None
        url = urljoin(ORIGIN, a["href"])
        m = _ID_RE.search(url)
        if not m:
            return None
        source_id = m.group(1)

        title_el = card.select_one(".c-products__title")
        title = title_el.get_text(" ", strip=True) if title_el else ""

        price_el = card.select_one(".c-products__price")
        price = _parse_price(price_el.get_text(" ", strip=True)) if price_el else None

        info_el = card.select_one(".c-products__info")
        address = info_el.get_text(" ", strip=True) if info_el else ""
        city, district = _split_address(address, cfg.search.mesto)

        # obrázek: lazy-load je v data-src (absolutní URL), fallback na src
        images: list[str] = []
        img_el = card.select_one(".c-products__img img")
        if img_el:
            src = img_el.get("data-src") or img_el.get("src")
            if src:
                images.append(urljoin(ORIGIN, src))

        return Listing(
            source=self.name,
            source_id=source_id,
            url=url,
            title=title,
            price=price,
            disposition=normalize_disposition(title),
            area=parse_area(title),
            address=address,
            city=city,
            district=district,
            images=images,
        )

    # --- doplnění detailu -------------------------------------------------

    def _enrich_detail(self, l: Listing, cfg: Config, http: Http, store: Store) -> None:
        cache_key = f"idnes:detail:{l.source_id}"
        det = store.cache_get(cache_key, max_age_days=cfg.detail_cache_dny)
        if det is None:
            try:
                r = http.get(l.url)
            except Exception as e:
                log.debug("iDNES detail %s chyba: %s", l.source_id, e)
                return
            det = self._extract_detail(r.text)
            store.cache_set(cache_key, det)

        # aplikace detailu na listing
        if det.get("area"):
            l.area = det["area"]
        l.balcony = bool(det.get("balcony"))
        l.terrace = bool(det.get("terrace"))
        l.loggia = bool(det.get("loggia"))
        l.garden = bool(det.get("garden"))
        l.description = det.get("description", "") or ""
        l.building_type = det.get("building_type")
        l.building_condition = det.get("building_condition")
        l.floor = det.get("floor")
        l.furnished = det.get("furnished")
        l.available_from = det.get("available_from")
        if det.get("elevator") is not None:
            l.elevator = det["elevator"]

    @staticmethod
    def _extract_detail(html: str) -> dict:
        """Z detailního HTML vytáhne jen to, co potřebujeme (a co půjde do cache)."""
        soup = BeautifulSoup(html, "lxml")

        # tabulka parametrů: páry <dt>název</dt><dd>hodnota</dd>
        scope = soup.select_one("div.b-definition-columns") or soup
        params: dict[str, str] = {}
        for dt in scope.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            key = dt.get_text(" ", strip=True).lower()
            if key:
                params[key] = dd.get_text(" ", strip=True) if dd else ""

        desc_el = soup.select_one(".b-desc")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""

        # venkovní prostor: strukturovaná tabulka NEBO popis (s ošetřením negace)
        balcony = _has_feature(params, "balkon", "balkón") or _in_desc(description, "balkon", "balkón")
        loggia = _has_feature(params, "lodžie", "lodzie") or _in_desc(description, "lodžie", "lodzie")
        terrace = _has_feature(params, "terasa") or _in_desc(description, "terasa", "terasy")
        garden = _has_feature(params, "zahrada", "předzahrádka") or _in_desc(description, "zahrad", "zahrádk")

        return {
            "area": parse_area(params.get("užitná plocha") or params.get("plocha") or ""),
            "balcony": balcony,
            "terrace": terrace,
            "loggia": loggia,
            "garden": garden,
            "description": description,
            "building_type": params.get("konstrukce budovy") or None,
            "building_condition": _clean_condition(params.get("stav bytu") or params.get("stav budovy")),
            "floor": _parse_floor(params.get("podlaží")),
            "furnished": params.get("vybavení") or None,
            "available_from": params.get("datum nastěhování") or None,
            "elevator": _parse_bool(params["výtah"]) if "výtah" in params else None,
        }


# --- pomocné funkce -------------------------------------------------------

def _parse_price(text: str | None) -> int | None:
    """'15 580 Kč/měsíc' -> 15580; 'Informace o ceně u RK' -> None."""
    if not text:
        return None
    head = text.split("Kč")[0]  # jen část před "Kč", ať nespojíme s dalšími čísly
    digits = re.sub(r"\D", "", head)
    return int(digits) if digits else None


def _split_address(address: str, default_city: str) -> tuple[str, str]:
    """Vytáhne OBEC (ne okres!) a část z adresy iDNES.
    'Durychova, Hradec Králové - Nový Hradec Králové'      -> ('Hradec Králové', 'Nový Hradec Králové')
    'Za Drahou, Nový Bydžov, okres Hradec Králové'         -> ('Nový Bydžov', '')
    'Chlumec nad Cidlinou - Chlumec I, okres Hradec Králové' -> ('Chlumec nad Cidlinou', 'Chlumec I')"""
    if not address:
        return "", ""
    # odřízni koncové ', okres …' a '… kraj' — to není obec
    a = re.sub(r",?\s*okres\s+.*$", "", address, flags=re.I)
    a = re.sub(r",?\s*\S+\s+kraj\s*$", "", a, flags=re.I)
    segs = [p.strip() for p in a.split(",") if p.strip()]
    if not segs:
        return "", ""
    tail = segs[-1]  # poslední segment = obec (příp. 'Obec - Část')
    if " - " in tail:
        city, district = tail.split(" - ", 1)
        return city.strip(), district.strip()
    return tail, ""


def _has_feature(params: dict, *keys: str) -> bool:
    """Prvek je přítomen, pokud existuje jeho řádek v tabulce (iDNES řádek uvádí jen když prvek je).
    Prázdná hodnota bývá jen zaškrtávací ikona; explicitní negaci přesto ošetříme."""
    for k in keys:
        if k in params:
            v = params[k].strip().lower()
            if v in ("ne", "není", "0", "žádná", "žádný"):
                return False
            return True
    return False


def _in_desc(desc: str, *words: str) -> bool:
    """Hledá slovo v popisu, ale přeskočí negovaný výskyt ('bez balkonu')."""
    d = (desc or "").lower()
    for w in words:
        idx = 0
        while True:
            i = d.find(w, idx)
            if i < 0:
                break
            if "bez " not in d[max(0, i - 6):i]:
                return True
            idx = i + len(w)
    return False


def _clean_condition(v: str | None) -> str | None:
    """'velmi dobrý stav' -> 'velmi dobrý' (kvůli napojení na scoring.CONDITION_SCORE)."""
    if not v:
        return None
    v = re.sub(r"\s*stav$", "", v.strip(), flags=re.I)
    return v or None


def _parse_floor(v: str | None) -> int | None:
    """'5. patro (6. NP)' -> 5."""
    if not v:
        return None
    m = re.search(r"(\d+)", v)
    return int(m.group(1)) if m else None


def _parse_bool(v: str | None) -> bool:
    """Řádek uvedený bez hodnoty (jen ikona) bereme jako 'ano'; 'ne'/'není' jako 'ne'."""
    s = (v or "").strip().lower()
    if s in ("ne", "není", "0"):
        return False
    return True
