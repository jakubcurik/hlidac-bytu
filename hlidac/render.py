"""Generování HTML dashboardu (přehledu) z nalezených bytů."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config
from .models import Listing

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "hlidac" / "templates"
OUTPUT_DIR = ROOT / "output"

SOURCE_LABELS = {
    "sreality": "Sreality",
    "bezrealitky": "Bezrealitky",
    "ulovdomov": "Ulovdomov",
    "idnes": "iDNES Reality",
}


def render_dashboard(
    listings: list[Listing],
    cfg: Config,
    new_keys: set[str] | None = None,
    out_path: str | Path | None = None,
) -> Path:
    new_keys = new_keys or set()
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = Path(out_path) if out_path else OUTPUT_DIR / "index.html"

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["tisic"] = lambda n: f"{n:,}".replace(",", " ") if n is not None else "?"
    tpl = env.get_template("dashboard.html.j2")

    # data pro karty
    cards = []
    for l in listings:
        cards.append({
            "key": l.key,
            "url": l.url,
            "title": l.title or f"{l.disposition} {l.city}".strip(),
            "price": l.price,
            "fees": l.fees,
            "fees_known": l.fees_known,
            "fees_estimated": l.fees_estimated,
            "total_price": l.total_price,
            "deposit": l.deposit,
            "commission": l.commission,
            "summary": l.summary,
            "area": int(l.area) if l.area else None,
            "disposition": l.disposition,
            "address": l.address or l.city,
            "city": l.city,
            "image": l.image,
            "score": l.score,
            "reasons": l.score_reasons,
            "outdoor": l.outdoor,
            "outdoor_label": l.outdoor_label,
            "building_type": l.building_type,
            "building_condition": l.building_condition,
            "price_per_m2": l.price_per_m2,
            "source": l.source,
            "source_label": SOURCE_LABELS.get(l.source, l.source),
            "is_new": l.key in new_keys,
            "available_from": l.available_from,
        })

    stats = {
        "total": len(cards),
        "new": sum(1 for c in cards if c["is_new"]),
        "outdoor": sum(1 for c in cards if c["outdoor"]),
        "by_source": {
            SOURCE_LABELS.get(s, s): sum(1 for c in cards if c["source"] == s)
            for s in SOURCE_LABELS
            if any(c["source"] == s for c in cards)
        },
    }

    html = tpl.render(
        cards=cards,
        stats=stats,
        cfg=cfg,
        generated=datetime.now().strftime("%-d. %-m. %Y %H:%M") if _supports_dash() else datetime.now().strftime("%d.%m.%Y %H:%M"),
    )
    out_path.write_text(html, encoding="utf-8")

    # vedle HTML ulož i JSON s daty (kdyby se hodil)
    (OUTPUT_DIR / "listings.json").write_text(
        json.dumps([l.to_dict() for l in listings], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return out_path


def _supports_dash() -> bool:
    """strftime('%-d') funguje na Linux/Mac, ne na Windows — bezpečně otestuj."""
    try:
        datetime.now().strftime("%-d")
        return True
    except ValueError:
        return False
