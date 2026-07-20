"""Scraper Ulovdomov.cz.

Ulovdomov je Next.js aplikace, ale inzeráty NEJSOU v __NEXT_DATA__ (tam je jen počet).
Data se načítají až client-side z veřejného JSON API https://ud.api.ulovdomov.cz.
Postup je proto dvoukrokový:
  1) geokódování města na bounding box: POST /fe-api/address/location2json
  2) vyhledávání: POST /v1/offer/find (offerType=rent, propertyType=flat, bounds)

Seznam z /offer/find už obsahuje cenu, plochu, dispozici, popis i venkovní prostor
(pole convenience), takže detail stahujeme jen kvůli upřesnění stavu/materiálu budovy
u kandidátů, kteří projdou levným předfiltrem (stejná logika jako u Sreality).
"""
from __future__ import annotations

import logging

from ..config import Config
from ..http import Http
from ..models import Listing, normalize_disposition, parse_area
from ..scoring import cheap_prefilter
from ..store import Store
from .base import Scraper

log = logging.getLogger("hlidac.ulovdomov")

API = "https://ud.api.ulovdomov.cz"
GEOCODE_URL = "https://www.ulovdomov.cz/fe-api/address/location2json"
FIND_URL = API + "/v1/offer/find"
DETAIL_URL = API + "/v1/offer/detail"

PER_PAGE = 30          # počet inzerátů na stránku
SORTING = "latest"     # cheapest | latest | best — nejnovější první (hlídač nových)

# Kód dispozice z API -> lidský tvar (normalize_disposition ho pak stejně sjednotí).
_DISPOSITION = {
    "onePlusKk": "1+kk", "onePlusOne": "1+1",
    "twoPlusKk": "2+kk", "twoPlusOne": "2+1",
    "threePlusKk": "3+kk", "threePlusOne": "3+1",
    "fourPlusKk": "4+kk", "fourPlusOne": "4+1",
    "fivePlusKk": "5+kk", "fivePlusOne": "5+1",
    "sixAndMore": "6+kk", "atypical": "atypicky",
}

# Bounding box celé ČR — fallback, když geokódování města selže.
_CR_BOUNDS = {
    "northEast": {"lat": 51.06, "lng": 18.87},
    "southWest": {"lat": 48.55, "lng": 12.09},
}

# Negace před klíčovým slovem, ať "bez balkonu" nezaloží balcony=True.
_NEG = ("bez ", "není ", "žádn", "nemá ", "vyjma ")


def _mentions(text: str, *keywords: str) -> bool:
    """Obsahuje text některé klíčové slovo mimo negovaný kontext (16 znaků před ním)?"""
    t = text.lower()
    for kw in keywords:
        i = 0
        while True:
            p = t.find(kw, i)
            if p < 0:
                break
            if not any(n in t[max(0, p - 16):p] for n in _NEG):
                return True
            i = p + len(kw)
    return False


def _param(params: dict, key: str):
    """Z detail parametru vytáhne hodnotu — buď title z options, nebo prosté value."""
    p = params.get(key)
    if not isinstance(p, dict):
        return None
    if p.get("options"):
        return p["options"][0].get("title")
    return p.get("value")


class UlovdomovScraper(Scraper):
    name = "ulovdomov"

    def fetch(self, cfg: Config, http: Http, store: Store) -> list[Listing]:
        bounds = self._geocode(cfg, http)
        listings: list[Listing] = []
        seen_ids: set[str] = set()

        for page in range(1, cfg.max_stran_na_zdroj + 1):
            body = {"offerType": "rent", "propertyType": "flat", "bounds": bounds}
            params = {"page": page, "perPage": PER_PAGE, "sorting": SORTING}
            try:
                r = http.post(FIND_URL, params=params, json=body)
            except Exception as e:
                log.warning("Ulovdomov: chyba stránky %s: %s", page, e)
                break
            payload = r.json() or {}
            offers = (payload.get("data") or {}).get("offers") or []
            if not offers:
                break

            for o in offers:
                l = self._parse_offer(o)
                if l and l.source_id not in seen_ids:
                    seen_ids.add(l.source_id)
                    listings.append(l)

            total_pages = (payload.get("extraData") or {}).get("totalPages") or page
            if page >= total_pages:
                break

        # doplnění detailů jen pro kandidáty, kteří projdou levným předfiltrem
        candidates = [l for l in listings if cheap_prefilter(l, cfg)]
        log.info("Ulovdomov: %d inzerátů, %d kandidátů na detail", len(listings), len(candidates))
        for l in candidates:
            self._enrich_detail(l, cfg, http, store)

        return candidates

    # --- geokódování ------------------------------------------------------

    def _geocode(self, cfg: Config, http: Http) -> dict:
        """Vrátí bounding box města přes location2json. Fallback: bounds celé ČR."""
        try:
            r = http.post(GEOCODE_URL, json={"location": cfg.search.mesto})
            geo = ((r.json() or {}).get("json") or {}).get("geometry") or {}
            b = geo.get("bounds") or geo.get("viewport")
            if b:
                return {
                    "northEast": {"lat": b["northeast"]["lat"], "lng": b["northeast"]["lng"]},
                    "southWest": {"lat": b["southwest"]["lat"], "lng": b["southwest"]["lng"]},
                }
        except Exception as e:
            log.warning("Ulovdomov: geokódování '%s' selhalo: %s", cfg.search.mesto, e)
        return _CR_BOUNDS

    # --- parsování seznamu ------------------------------------------------

    def _parse_offer(self, o: dict) -> Listing | None:
        oid = o.get("id")
        if not oid:
            return None

        price = (o.get("rentalPrice") or {}).get("value")
        mf = o.get("monthlyFeesPrice")
        fees = mf.get("value") if isinstance(mf, dict) else None
        geo = o.get("geoCoordinates") or {}
        images = [p["path"] for p in (o.get("photos") or []) if p.get("path")]

        disp = normalize_disposition(
            _DISPOSITION.get(o.get("disposition"), o.get("disposition") or "")
        )
        area = o.get("area")
        area = float(area) if isinstance(area, (int, float)) else parse_area(o.get("title"))

        street = (o.get("street") or {}).get("title")
        city = (o.get("village") or {}).get("title") or ""
        district = (o.get("villagePart") or {}).get("title") or ""

        l = Listing(
            source=self.name,
            source_id=str(oid),
            url=o.get("absoluteUrl") or "",
            title=o.get("title", "") or "",
            price=int(price) if price else None,
            fees=int(fees) if fees else None,
            price_note=o.get("priceNote", "") or "",
            disposition=disp,
            area=area,
            address=", ".join(p for p in [street, city] if p),
            city=city,
            district=district,
            lat=geo.get("lat"),
            lon=geo.get("lng"),
            images=images[:6],
            description=o.get("description", "") or "",
            floor=o.get("floorLevel") if isinstance(o.get("floorLevel"), int) else None,
            available_from=o.get("availableFrom"),
        )

        # Venkovní prostor: balkon/terasa/lodžie ze strukturovaného convenience jsou spolehlivé.
        conv = set((o.get("convenience") or []) + (o.get("houseConvenience") or []))
        l.balcony = "balcony" in conv
        l.terrace = "terrace" in conv
        l.loggia = "loggia" in conv
        l.elevator = True if "lift" in conv else None

        # Doplnění z popisu (s ošetřením negace) — zachytí, co v convenience chybí.
        # Pozn.: "garden" v convenience je u Ulovdomova artefakt (hlásí se u všech bytů),
        # takže zahradu bereme jen z konzervativní detekce v textu.
        text = f"{l.title} {l.description}"
        if not l.balcony and _mentions(text, "balkon", "balkón"):
            l.balcony = True
        if not l.terrace and _mentions(text, "teras"):
            l.terrace = True
        if not l.loggia and _mentions(text, "lodž", "lodz", "loggi"):
            l.loggia = True
        if _mentions(text, "zahrádk", "předzahrád", "se zahrad", "vlastní zahrad",
                     "k zahrad", "na zahrad", "užívání zahrad"):
            l.garden = True

        return l

    # --- doplnění detailu -------------------------------------------------

    def _enrich_detail(self, l: Listing, cfg: Config, http: Http, store: Store) -> None:
        cache_key = f"ulovdomov:detail:{l.source_id}"
        det = store.cache_get(cache_key, max_age_days=cfg.detail_cache_dny)
        if det is None:
            try:
                r = http.get(DETAIL_URL, params={"offerId": l.source_id})
            except Exception as e:
                log.debug("Ulovdomov detail %s chyba: %s", l.source_id, e)
                return
            data = (r.json() or {}).get("data")
            if not data:
                return
            det = self._extract_detail(data)
            store.cache_set(cache_key, det)

        # Upřesnění ze strukturovaného detailu (seznam přebíjíme jen tam, kde detail něco dá).
        if det.get("usableArea"):
            l.area = det["usableArea"]
        if isinstance(det.get("floor"), int):
            l.floor = det["floor"]
        l.building_condition = det.get("building_condition") or l.building_condition
        l.building_type = det.get("building_type") or l.building_type
        l.furnished = det.get("furnished") or l.furnished
        if det.get("fees"):
            l.fees = det["fees"]

        # Venkovní prostor: detail jen PŘIDÁVÁ jistotu, nikdy nemaže signál ze seznamu/popisu.
        if det.get("balcony"):
            l.balcony = True
        if det.get("terrace"):
            l.terrace = True
        if det.get("loggia"):
            l.loggia = True
        if det.get("garden"):
            l.garden = True

    @staticmethod
    def _extract_detail(data: dict) -> dict:
        """Z detailního JSON vytáhne jen to, co jde do cache a doplňuje seznam."""
        p = data.get("parameters") or {}
        fee = (data.get("monthlyFeePrice") or {})
        return {
            "usableArea": parse_area(_param(p, "usableArea")) or parse_area(_param(p, "floorArea")),
            "floor": _param(p, "floorNumber"),
            "building_condition": _param(p, "buildingCondition"),
            "building_type": _param(p, "material"),   # "cihlová"/"panelová" — scoring hledá materiál
            "furnished": _param(p, "furnished"),
            "balcony": _param(p, "balcony") == "Ano",
            "terrace": _param(p, "terrace") == "Ano",
            "loggia": _param(p, "loggia") == "Ano",
            "garden": (parse_area(_param(p, "gardenArea")) or 0) > 0,
            "fees": fee.get("value") if isinstance(fee, dict) else None,
        }
