"""Scraper Bezrealitky.cz.

Bezrealitky mají veřejné GraphQL API (https://api.bezrealitky.cz/graphql/),
které odpovídá obyčejnému POSTu bez klíče a bez antibotu.

Postup:
  1) přes regionByUri zjistíme OSM id města (Hradec Králové -> 439071),
  2) přes listAdverts stáhneme nájmy bytů s filtrem na cenu, se stránkováním.
Venkovní prostor je přímo ve strukturovaných polích (balconySurface, terraceSurface,
loggiaSurface, frontGarden) — není potřeba stahovat detail.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import Config
from ..http import Http
from ..models import Listing, normalize_disposition
from ..store import Store
from .base import Scraper, slugify

log = logging.getLogger("hlidac.bezrealitky")

GRAPHQL = "https://api.bezrealitky.cz/graphql/"
DETAIL_URL = "https://www.bezrealitky.cz/nemovitosti-byty-domy/{uri}"
PAGE_SIZE = 50

# DISP_2_KK -> "2+kk"
DISPOSITION_MAP = {
    "GARSONIERA": "garsoniera", "OSTATNI": "atypicky", "UNDEFINED": "",
    "DISP_1_KK": "1+kk", "DISP_1_1": "1+1",
    "DISP_2_KK": "2+kk", "DISP_2_1": "2+1",
    "DISP_3_KK": "3+kk", "DISP_3_1": "3+1",
    "DISP_4_KK": "4+kk", "DISP_4_1": "4+1",
    "DISP_5_KK": "5+kk", "DISP_5_1": "5+1",
    "DISP_6_KK": "6+kk", "DISP_6_1": "6+1",
    "DISP_7_KK": "7+kk", "DISP_7_1": "7+1",
}
CONSTRUCTION_MAP = {
    "BRICK": "Cihlová", "PANEL": "Panelová", "STONE": "Kamenná",
    "MIXED": "Smíšená", "SKELETON": "Skeletová", "WOOD": "Dřevěná",
    "ASSEMBLED": "Montovaná", "LOW_ENERGY": "Nízkoenergetická",
}
CONDITION_MAP = {
    "NEW_BUILDING": "Novostavba", "VERY_GOOD": "Velmi dobrý", "GOOD": "Dobrý",
    "AFTER_RECONSTRUCTION": "Po rekonstrukci", "BEFORE_RECONSTRUCTION": "Před rekonstrukcí",
    "UNDER_CONSTRUCTION": "Ve výstavbě", "DEVELOPMENT_PROJECT": "Projekt",
    "WRONG_STATE": "Špatný", "BAD": "Špatný", "TO_DEMOLITION": "K demolici",
}

REGION_QUERY = "query($u:String!){ regionByUri(uri:$u, locale:CS){ id name osmId } }"

LIST_QUERY = """
query($osm:[ID], $priceTo:Int, $limit:Int, $offset:Int){
  listAdverts(
    offerType:[PRONAJEM], estateType:[BYT], regionOsmIds:$osm,
    priceTo:$priceTo, limit:$limit, offset:$offset, order:TIMEORDER_DESC
  ){
    totalCount
    list{
      id uri title price charges surface disposition
      address(locale: CS) city(locale: CS)
      balconySurface terraceSurface loggiaSurface frontGarden
      construction condition availableFrom etage
      gps{ lat lng }
      mainImage{ url(filter: RECORD_MAIN) }
    }
  }
}
"""


class BezrealitkyScraper(Scraper):
    name = "bezrealitky"

    def fetch(self, cfg: Config, http: Http, store: Store) -> list[Listing]:
        osm_id = self._region_osm(cfg, http, store)
        if not osm_id:
            log.warning("Bezrealitky: nepodařilo se zjistit region pro '%s'.", cfg.search.mesto)
            return []

        listings: list[Listing] = []
        offset = 0
        for _ in range(cfg.max_stran_na_zdroj):
            data = self._gql(http, LIST_QUERY, {
                "osm": [f"R{osm_id}"],
                "priceTo": int(cfg.search.max_cena),
                "limit": PAGE_SIZE,
                "offset": offset,
            })
            if not data:
                break
            la = data.get("listAdverts") or {}
            batch = la.get("list") or []
            for adv in batch:
                l = self._parse(adv)
                if l:
                    listings.append(l)
            offset += PAGE_SIZE
            if offset >= (la.get("totalCount") or 0) or not batch:
                break

        log.info("Bezrealitky: nalezeno %d inzerátů.", len(listings))
        return listings

    # --- pomocné ----------------------------------------------------------

    def _region_osm(self, cfg: Config, http: Http, store: Store) -> int | None:
        uri = slugify(cfg.search.mesto)
        cache_key = f"bezrealitky:region:{uri}"
        cached = store.cache_get(cache_key, max_age_days=90)
        if cached and cached.get("osmId"):
            return cached["osmId"]
        data = self._gql(http, REGION_QUERY, {"u": uri})
        region = (data or {}).get("regionByUri")
        if region and region.get("osmId"):
            store.cache_set(cache_key, region)
            return region["osmId"]
        return None

    def _gql(self, http: Http, query: str, variables: dict) -> dict | None:
        try:
            r = http.post(GRAPHQL, json={"query": query, "variables": variables},
                          headers={"Origin": "https://www.bezrealitky.cz"})
            payload = r.json()
        except Exception as e:
            log.warning("Bezrealitky GraphQL chyba: %s", e)
            return None
        if payload.get("errors"):
            log.warning("Bezrealitky GraphQL errors: %s", str(payload["errors"])[:200])
            return None
        return payload.get("data")

    def _parse(self, adv: dict) -> Listing | None:
        rid = adv.get("id")
        if not rid:
            return None
        uri = adv.get("uri") or rid
        gps = adv.get("gps") or {}
        img = (adv.get("mainImage") or {}).get("url")
        disp_raw = adv.get("disposition") or ""
        disposition = DISPOSITION_MAP.get(disp_raw) or normalize_disposition(disp_raw)

        return Listing(
            source=self.name,
            source_id=str(rid),
            url=DETAIL_URL.format(uri=uri),
            title=adv.get("title") or "",
            price=adv.get("price"),
            fees=adv.get("charges"),
            disposition=disposition,
            area=float(adv["surface"]) if adv.get("surface") else None,
            address=adv.get("address") or "",
            city=adv.get("city") or "",
            lat=gps.get("lat"),
            lon=gps.get("lng"),
            images=[img] if img else [],
            balcony=bool(adv.get("balconySurface")),
            terrace=bool(adv.get("terraceSurface")),
            loggia=bool(adv.get("loggiaSurface")),
            garden=bool(adv.get("frontGarden")),
            building_type=CONSTRUCTION_MAP.get(adv.get("construction") or ""),
            building_condition=CONDITION_MAP.get(adv.get("condition") or ""),
            floor=adv.get("etage"),
            available_from=self._date(adv.get("availableFrom")),
            # datum vložení Bezrealitky API anonymně nedává ("Access denied to this field"),
            # listed_at zůstává None -> dashboard poctivě ukáže, kdy inzerát zachytil hlídač
        )

    @staticmethod
    def _date(ts) -> str | None:
        """availableFrom je unixový timestamp -> 'YYYY-MM-DD'."""
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, TypeError):
            return None

