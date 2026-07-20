"""Scraper Sreality.cz.

Sreality je Next.js aplikace — data jsou server-side v <script id="__NEXT_DATA__">.
Žádné API klíče, žádný antibot: stačí stáhnout HTML stránku výsledků a detailu.

Seznam výsledků NEobsahuje balkon/terasu/popis — ty jsou až na detailu bytu,
takže detail stahujeme jen pro byty, které projdou levným předfiltrem (cena/dispozice).
"""
from __future__ import annotations

import logging
import re

from ..config import Config
from ..http import Http
from ..models import Listing, parse_area
from ..scoring import cheap_prefilter
from ..store import Store
from .base import Scraper, extract_next_data, slugify

log = logging.getLogger("hlidac.sreality")

SEARCH_URL = "https://www.sreality.cz/hledani/pronajem/byty/{city}"
DETAIL_URL = "https://www.sreality.cz/detail/pronajem/byt/{sub}/{city}/{id}"

# Obrázky ze Seznamího CDN (sdn.cz) vyžadují transformační parametr, jinak vrací 401.
# Tenhle formát reálně používá web Sreality při zobrazení (resize + webp).
IMG_TRANSFORM = "?fl=res,800,600,3|shr,,20|webp,60"


def _image_url(raw: str | None) -> str | None:
    """Doplní protokol a transformační parametr, aby byl obrázek zobrazitelný."""
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    return raw + IMG_TRANSFORM


def _find_query(nd: dict, key0: str):
    """Najde v TanStack cache dotaz, jehož queryKey[0] == key0, a vrátí jeho data."""
    try:
        queries = nd["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError):
        return None
    for q in queries:
        qk = q.get("queryKey")
        if isinstance(qk, list) and qk and qk[0] == key0:
            return q.get("state", {}).get("data")
    return None


class SrealityScraper(Scraper):
    name = "sreality"

    def fetch(self, cfg: Config, http: Http, store: Store) -> list[Listing]:
        city_seo = slugify(cfg.search.mesto)
        listings: list[Listing] = []
        seen_ids: set[str] = set()

        for page in range(1, cfg.max_stran_na_zdroj + 1):
            url = SEARCH_URL.format(city=city_seo)
            params = {"strana": page} if page > 1 else {}
            try:
                r = http.get(url, params=params)
            except Exception as e:
                log.warning("Sreality: chyba stránky %s: %s", page, e)
                break
            nd = extract_next_data(r.text)
            data = _find_query(nd, "estatesSearch") if nd else None
            if not data or not data.get("results"):
                break

            for res in data["results"]:
                l = self._parse_list_item(res, city_seo)
                if l and l.source_id not in seen_ids:
                    seen_ids.add(l.source_id)
                    listings.append(l)

            pag = data.get("pagination", {})
            if pag.get("offset", 0) + pag.get("limit", 22) >= pag.get("total", 0):
                break

        # doplnění detailů jen pro kandidáty, kteří projdou levným předfiltrem
        candidates = [l for l in listings if cheap_prefilter(l, cfg)]
        log.info("Sreality: %d inzerátů, %d kandidátů na detail", len(listings), len(candidates))
        for l in candidates:
            self._enrich_detail(l, cfg, http, store, city_seo)

        return candidates

    # --- parsování seznamu ------------------------------------------------

    def _parse_list_item(self, res: dict, city_seo: str) -> Listing | None:
        rid = res.get("id")
        if not rid:
            return None
        sub = (res.get("categorySubCb") or {}).get("name", "")
        loc = res.get("locality") or {}
        price = res.get("priceSummaryCzk") or res.get("priceCzk")
        images = [
            u for img in (res.get("images") or [])
            if (u := _image_url(img.get("url")))
        ]
        detail_city = loc.get("citySeoName") or city_seo
        url = DETAIL_URL.format(sub=(sub or "byt"), city=detail_city, id=rid)
        return Listing(
            source=self.name,
            source_id=str(rid),
            url=url,
            title=res.get("name", ""),
            price=int(price) if price else None,
            disposition=sub,
            area=parse_area(res.get("name")),
            city=loc.get("city", ""),
            district=loc.get("district", ""),
            address=", ".join(
                p for p in [loc.get("street"), loc.get("city")] if p
            ),
            lat=loc.get("latitude"),
            lon=loc.get("longitude"),
            images=images[:6],
        )

    # --- doplnění detailu -------------------------------------------------

    def _enrich_detail(self, l: Listing, cfg: Config, http: Http, store: Store, city_seo: str) -> None:
        cache_key = f"sreality:detail:{l.source_id}"
        det = store.cache_get(cache_key, max_age_days=cfg.detail_cache_dny)
        if det is None:
            sub = l.disposition or "byt"
            url = DETAIL_URL.format(sub=sub, city=(l.city and slugify(l.city)) or city_seo, id=l.source_id)
            try:
                r = http.get(url)
            except Exception as e:
                log.debug("Sreality detail %s chyba: %s", l.source_id, e)
                return
            nd = extract_next_data(r.text)
            data = _find_query(nd, "estate") if nd else None
            if not data:
                return
            det = self._extract_detail(data)
            store.cache_set(cache_key, det)

        # aplikace detailu na listing
        p = det
        if p.get("usableArea"):
            l.area = float(p["usableArea"])
        l.balcony = bool(p.get("balcony"))
        l.terrace = bool(p.get("terrace"))
        l.loggia = bool(p.get("loggia"))
        l.garden = bool(p.get("gardenArea"))
        l.description = p.get("description", "") or ""
        l.building_condition = p.get("building_condition")
        l.building_type = p.get("building_type")
        l.floor = p.get("floor")
        l.furnished = p.get("furnished")
        l.available_from = p.get("available_from")
        if p.get("fees"):
            l.fees = p["fees"]

    @staticmethod
    def _extract_detail(data: dict) -> dict:
        """Z detailního JSON vytáhne jen to, co potřebujeme (a co půjde do cache)."""
        params = data.get("params") or {}

        def name_of(field):
            v = params.get(field)
            return v.get("name") if isinstance(v, dict) else None

        # služby / náklady na bydlení -> číslo, pokud to jde (bývá číslo, občas text)
        fees = None
        col = params.get("costOfLiving")
        if isinstance(col, (int, float)) and col:
            fees = int(col)
        elif isinstance(col, str) and col.strip():
            digits = re.sub(r"\D", "", col)
            fees = int(digits) if digits else None

        return {
            "usableArea": params.get("usableArea"),
            "balcony": bool(params.get("balcony")),
            "terrace": bool(params.get("terrace")),
            "loggia": bool(params.get("loggia")),
            "gardenArea": params.get("gardenArea"),
            "description": data.get("description", "") or "",
            "building_condition": name_of("buildingCondition"),
            "building_type": name_of("buildingType"),
            "floor": params.get("floorNumber"),
            "furnished": name_of("furnished"),
            "available_from": params.get("readyDate"),
            "fees": fees,
        }
