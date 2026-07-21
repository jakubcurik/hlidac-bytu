"""Generování HTML dashboardu (přehledu) z nalezených bytů."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config
from .models import Listing, disposition_rank

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "hlidac" / "templates"
OUTPUT_DIR = ROOT / "output"

SOURCE_LABELS = {
    "sreality": "Sreality",
    "bezrealitky": "Bezrealitky",
    "ulovdomov": "Ulovdomov",
    "idnes": "iDNES Reality",
}


def _age(iso: str | None) -> tuple[int, str]:
    """(stáří ve dnech, český popisek) z ISO data/času.
    'dnes' / 'včera' / 'před X dny' / u starších konkrétní datum."""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return 9999, ""
    if dt.tzinfo is None:  # holé datum 'YYYY-MM-DD' z portálu — ber jako UTC den
        dt = dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc).date() - dt.astimezone(timezone.utc).date()).days
    if days <= 0:
        return 0, "dnes"
    if days == 1:
        return 1, "včera"
    if days <= 14:
        return days, f"před {days} dny"
    return days, f"{dt.day}. {dt.month}."


def _building_class(building_type: str | None) -> str:
    """Zatřídí typ stavby pro filtrování v dashboardu: 'cihla' / 'panel' / 'jine' / ''."""
    if not building_type:
        return ""
    bt = building_type.strip().lower()
    if "cihl" in bt:
        return "cihla"
    if "panel" in bt:
        return "panel"
    return "jine"


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
        # stáří: preferuj datum z portálu (listed_at), fallback = kdy ho hlídač poprvé zachytil
        age_days, age_label = _age(l.listed_at or l.first_seen)
        if l.listed_at:
            age_title = f"na portál vloženo / upraveno {l.listed_at}"
        else:
            age_title = f"portál datum neuvádí — hlídač inzerát poprvé zachytil {(l.first_seen or '')[:10]}"
        cards.append({
            "age_days": age_days,
            "age_label": age_label,
            "age_title": age_title,
            "key": l.key,
            "url": l.url,
            "title": l.title or f"{l.disposition} {l.city}".strip(),
            "price": l.price,
            "fees": l.fees,
            "fees_known": l.fees_known,
            "fees_estimated": l.fees_estimated,
            "fees_note": l.fees_note,
            "total_price": l.total_price,
            "deposit": l.deposit,
            "commission": l.commission,
            "summary": l.summary,
            "area": int(l.area) if l.area else None,
            "disposition": l.disposition,
            "disposition_rank": disposition_rank(l.disposition),
            "address": l.address or l.city,
            "city": l.city,
            "image": l.image,
            "score": l.score,
            "reasons": l.score_reasons,
            "outdoor": l.outdoor,
            "outdoor_qualifying": l.has_qualifying_outdoor(cfg.search.venkovni_typy),
            "outdoor_label": l.outdoor_label,
            "building_type": l.building_type,
            "building_class": _building_class(l.building_type),
            "building_condition": l.building_condition,
            "price_per_m2": l.price_per_m2,
            "pets": l.pets,
            "source": l.source,
            "source_label": SOURCE_LABELS.get(l.source, l.source),
            "is_new": l.key in new_keys,
            "available_from": l.available_from,
        })

    # nabídka dispozic pro filtr (seřazená dle ranku)
    dispositions = sorted(
        {c["disposition"] for c in cards if c["disposition"]},
        key=lambda d: disposition_rank(d),
    )

    stats = {
        "total": len(cards),
        "new": sum(1 for c in cards if c["is_new"]),
        "outdoor": sum(1 for c in cards if c["outdoor_qualifying"]),
        "estimated": sum(1 for c in cards if c["fees_estimated"]),
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
        dispositions=dispositions,
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
