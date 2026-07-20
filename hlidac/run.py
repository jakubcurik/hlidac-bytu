"""Orchestrátor — spojí scrapery, filtr, úložiště, dashboard a Telegram."""
from __future__ import annotations

import logging

from . import llm, notify, scoring
from .config import Config, load_config
from .http import Http
from .models import Listing
from .render import render_dashboard
from .store import Store

log = logging.getLogger("hlidac.run")


def _registry() -> dict:
    """Sestaví mapu název->třída scraperu. Chybějící scraper se přeskočí."""
    reg: dict = {}
    from .scrapers.sreality import SrealityScraper
    reg["sreality"] = SrealityScraper
    try:
        from .scrapers.bezrealitky import BezrealitkyScraper
        reg["bezrealitky"] = BezrealitkyScraper
    except Exception as e:  # scraper zatím neexistuje / má chybu importu
        log.debug("bezrealitky nedostupný: %s", e)
    try:
        from .scrapers.ulovdomov import UlovdomovScraper
        reg["ulovdomov"] = UlovdomovScraper
    except Exception as e:
        log.debug("ulovdomov nedostupný: %s", e)
    try:
        from .scrapers.idnes import IdnesScraper
        reg["idnes"] = IdnesScraper
    except Exception as e:
        log.debug("idnes nedostupný: %s", e)
    return reg


def _dedupe(listings: list[Listing]) -> list[Listing]:
    """Odstraní zjevné duplicity téhož bytu napříč portály.
    Shoda = stejná dispozice + plocha (na m²) + cena. Ponechá lépe obodovaný."""
    listings = sorted(listings, key=lambda x: x.score, reverse=True)
    seen: set[tuple] = set()
    out: list[Listing] = []
    for l in listings:
        sig = (l.disposition, int(l.area) if l.area else None, l.price)
        if sig != (None, None, None) and sig in seen and l.area and l.price:
            continue
        seen.add(sig)
        out.append(l)
    return out


def run(config_path: str | None = None, only_source: str | None = None) -> dict:
    cfg = load_config(config_path)
    store = Store()
    http = Http()

    first_run = store.total_count() == 0
    reg = _registry()

    sources = [only_source] if only_source else [s for s, on in cfg.zdroje.items() if on]

    all_listings: list[Listing] = []
    for name in sources:
        cls = reg.get(name)
        if not cls:
            log.warning("Zdroj '%s' není k dispozici, přeskakuji.", name)
            continue
        log.info("=== Zdroj: %s ===", name)
        try:
            found = cls().fetch(cfg, http, store)
            log.info("%s: nalezeno %d vyhovujících (před filtrem).", name, len(found))
            all_listings += found
        except Exception as e:
            log.error("Zdroj '%s' selhal: %s", name, e, exc_info=True)

    # LLM zpracování (spolehlivé poplatky/energie z popisu) — jen na kandidáty, s cache, paralelně
    if cfg.pouzit_llm and llm.available():
        cand = [l for l in all_listings if scoring.cheap_prefilter(l, cfg)]
        log.info("LLM (%s): zpracovávám %d inzerátů…", llm.provider_name(), len(cand))
        done = llm.enrich_many(cand, store, cfg)
        log.info("LLM: doplněno %d inzerátů.", done)
    elif cfg.pouzit_llm:
        log.info("LLM přeskočeno — chybí API klíč (GEMINI_API_KEY / OPENAI_API_KEY / GROQ_API_KEY v .env).")

    processed = scoring.process(all_listings, cfg)
    processed = _dedupe(processed)
    log.info("Po filtru, deduplikaci a bodování: %d bytů.", len(processed))

    new_keys: set[str] = set()
    for l in processed:
        if store.upsert(l):
            new_keys.add(l.key)
    zmizelo = store.mark_inactive_except({l.key for l in processed})

    out_path = render_dashboard(processed, cfg, new_keys)
    log.info("Dashboard: %s", out_path)

    to_notify = store.unnotified()
    notified = notify.notify_new(cfg, to_notify, first_run=first_run)
    store.mark_notified(notified)

    log.info("Hotovo. Celkem %d bytů, %d nových, %d zmizelo.",
             len(processed), len(new_keys), zmizelo)

    http.close()
    store.close()
    return {
        "total": len(processed),
        "new": len(new_keys),
        "gone": zmizelo,
        "dashboard": str(out_path),
        "listings": processed,
    }
