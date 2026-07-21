"""Filtrování a bodování inzerátů podle kritérií kamarádky.

Hlavní priorita = VENKOVNÍ PROSTOR (balkon/terasa/lodžie/zahrada).
Dále: rozumná cena, plocha ideálně 30 m²+, slušné zázemí (ne umakart / panel v rozpadu).
"""
from __future__ import annotations

import re

from .config import Config
from .models import Listing, city_key, disposition_rank, parse_fees_from_text, fees_included_in_rent

# Klíčová slova v popisu — kvalita zázemí
POSITIVE_KW = {
    "novostavb": 6, "po rekonstrukci": 6, "kompletní rekonstrukc": 7,
    "nová kuchyň": 4, "nová kuchyn": 4, "zděné jádro": 5, "zdene jadro": 5,
    "nové jádro": 4, "kuchyňská linka": 2, "kuchynska linka": 2,
    "zrekonstruovan": 5, "moderní": 2, "modern": 1,
}
NEGATIVE_KW = {
    "umakart": 10, "původní stav": 6, "puvodni stav": 6,
    "k rekonstrukci": 8, "před rekonstrukcí": 8, "pred rekonstrukci": 8,
    "nutná rekonstrukce": 8, "bytové jádro": 2,  # umakartové jádro
}

# Stav budovy z portálu -> bodový posun
CONDITION_SCORE = {
    "novostavba": 8, "velmi dobrý": 6, "po rekonstrukci": 7, "po částečné rekonstrukci": 4,
    "dobrý": 3, "ve výstavbě": 4, "projekt": 2,
    "před rekonstrukcí": -8, "špatný": -10, "k demolici": -20,
}


def allowed_cities(cfg: Config) -> set[str]:
    """Množina povolených obcí (znormalizovaně) = hledané město + volitelné okolí."""
    cities = {city_key(cfg.search.mesto)}
    cities |= {city_key(x) for x in cfg.search.okoli}
    return {c for c in cities if c}


def _strip_region(key: str) -> str:
    """Odstraní z názvu region (okres/kraj), ať 'okres hradec kralove' nefalešně nesedí na město."""
    key = re.sub(r"okres\s+.*$", "", key)
    key = re.sub(r"\S+\s+kraj\s*$", "", key)
    return key.strip(" ,")


def passes_locality(listing: Listing, cfg: Config) -> bool:
    """Přísný filtr lokality — jen hledané město (vč. jeho čtvrtí), ne okolní obce ani okres.
    Čtvrti jako Nový Hradec Králové mají city='Hradec Králové', takže projdou.
    'okres Hradec Králové' NEznamená město — takové (Chlumec, Nový Bydžov…) se vyřadí."""
    allowed = allowed_cities(cfg)
    if not allowed:
        return True
    # Podřetězec kvůli čtvrtím ('novy hradec kralove' obsahuje 'hradec kralove'),
    # ale nejdřív vyřízneme region, ať '…, okres Hradec Králové' nesedí falešně.
    ck = _strip_region(city_key(listing.city))
    if ck:
        return any(a in ck for a in allowed)
    # fallback na adresu, když city chybí
    ak = _strip_region(city_key(listing.address))
    if ak:
        return any(a in ak for a in allowed)
    return False  # neznámá lokalita -> přísně vyřadit (chceme opravdu jen Hradec)


# --- Fallback detekce z textu (když LLM nedoběhne) ------------------------

# Rezervace/obsazenost — konzervativně, ať se nechytá "rezervované parkovací stání".
_RESERVED_RE = re.compile(
    r"\bREZERVOV[ÁA]NO\b"
    r"|(?:byt|inzer\w*|nemovitost\w*)\s+(?:je\s+)?(?:již\s+)?(?:rezervov|obsazen|pronajat)"
    r"|již\s+(?:pronajat|obsazen|rezervov)"
    r"|(?:rezervov|pronajat)\w*\.?\s*(?:dě?kuj|neaktuáln)",
    re.I,
)
# Zvíře/mazlíček — společný kmen pro pets regexy.
_ANIMAL = r"(?:zvíř\w*|zvir\w*|mazlíč\w*|mazlic\w*)"
# Preference proti zvířatům (NE výslovný zákaz): "ideálně bez mazlíčků", "raději bez zvířat".
_PETS_PREF_RE = re.compile(
    r"(?:ideáln\w*|idealn\w*|raději|radeji|spíše|spise|přednostn\w*|prednostn\w*|upřednost\w*|uprednost\w*)"
    r"[^\n]{0,45}?bez\s+(?:domác\w*\s+)?" + _ANIMAL,  # tečka povolena kvůli zkratkám ("max. 2 osoby")
    re.I,
)
# Jednoznačný zákaz mazlíčků.
_PETS_BAN_RE = re.compile(
    r"bez\s+(?:domác\w*\s+)?" + _ANIMAL
    + r"|" + _ANIMAL + r"\s+(?:nejsou|není)\s+(?:povolen|vítán|možn|dovolen)"
    + r"|(?:zákaz|zakaz|nelze|nemožn\w*|nevhodn\w*\s+pro)\s+(?:chov\w*|drž\w*)?\s*(?:domác\w*\s+)?" + _ANIMAL
    + r"|" + _ANIMAL + r"\s+zakázán|no\s+pets",
    re.I,
)
# Mazlíčci po dohodě / podmíněně.
_PETS_MAYBE_RE = re.compile(
    _ANIMAL + r".{0,15}(?:po\s+dohodě|dle\s+dohody|po\s+domluvě)"
    r"|(?:po|dle)\s+(?:dohodě|domluvě).{0,15}" + _ANIMAL,
    re.I,
)


def estimate_fees_by_area(listing: Listing) -> int | None:
    """Hrubý odhad měsíčních záloh dle plochy — poslední záchrana, když nic jiného není.
    Záměrně konzervativní; vždy se označí jako odhad."""
    a = listing.area
    if not a:
        # bez plochy odhadneme podle dispozice (aspoň něco)
        rank = disposition_rank(listing.disposition)
        if rank and rank < 20:
            return 2600
        return 3400 if rank else None
    if a < 35:
        return 2600
    if a < 55:
        return 3200
    if a < 75:
        return 3800
    return 4500


def infer_fees(listing: Listing, cfg: Config) -> None:
    """Doplní poplatky, když je LLM nedodalo. Pořadí: regex z textu → heuristický odhad.
    Vše se jasně označí přes fees_estimated / fees_note."""
    if listing.fees is not None:
        return
    text = " ".join(p for p in [listing.price_note, listing.description] if p)
    # energie už v nájmu?
    if fees_included_in_rent(text):
        listing.fees = 0
        listing.fees_estimated = False
        listing.fees_note = "energie/služby v ceně nájmu"
        return
    fee = parse_fees_from_text(text, rent=listing.price)
    if fee:
        listing.fees = fee
        listing.fees_estimated = False  # konkrétní částka z textu
        listing.fees_note = "z popisu"
        return
    # poslední záchrana: odhad dle plochy (jen pokud povoleno)
    if cfg.odhad_poplatku:
        est = estimate_fees_by_area(listing)
        if est:
            listing.fees = est
            listing.fees_estimated = True
            listing.fees_note = "hrubý odhad dle plochy"


def infer_flags(listing: Listing) -> None:
    """Doplní rezervaci a postoj k mazlíčkům z textu, pokud je LLM nezjistilo."""
    text = " ".join(p for p in [listing.title, listing.price_note, listing.description] if p)
    if not listing.reserved and _RESERVED_RE.search(text):
        listing.reserved = True
    if listing.pets is None:
        # pořadí je důležité: "ideálně bez mazlíčků" by jinak spadlo do zákazu
        if _PETS_PREF_RE.search(text):
            listing.pets = "nezadouci"
        elif _PETS_BAN_RE.search(text):
            listing.pets = "zakaz"
        elif _PETS_MAYBE_RE.search(text):
            listing.pets = "po_dohode"


def cheap_prefilter(listing: Listing, cfg: Config) -> bool:
    """Levný předfiltr PŘED stažením detailu — ať nestahujeme detaily beznadějných bytů.
    Řešíme lokalitu, cenu a dispozici (plochu ne — v seznamu často chybí)."""
    c = cfg.search
    if not passes_locality(listing, cfg):
        return False
    # v této fázi ještě nemusíme znát poplatky, filtrujeme na základní cenu s rezervou
    if listing.price is not None and listing.price > c.max_cena * 1.05:
        return False
    rank = disposition_rank(listing.disposition)
    if rank and rank < disposition_rank(c.min_dispozice):
        return False
    return True


def passes_hard_filter(listing: Listing, cfg: Config) -> bool:
    """Tvrdé podmínky — když neprojde, byt se nezobrazí vůbec."""
    c = cfg.search
    # jen hledané město
    if not passes_locality(listing, cfg):
        return False
    # rezervované / obsazené pryč (pro kamarádku k ničemu)
    if c.vyloucit_rezervovane and listing.reserved:
        return False
    # jasný zákaz mazlíčků pryč (dle nastavené přísnosti)
    if c.mazlicci_filtr == "jen_zakaz" and listing.pets == "zakaz":
        return False
    if c.mazlicci_filtr == "vse" and listing.pets in ("zakaz", "po_dohode"):
        return False
    # CELKOVÁ cena (nájem + poplatky, pokud známy) musí být v rozpočtu
    if listing.total_price is None or listing.total_price > c.max_cena:
        return False
    # dispozice aspoň minimální (neznámou nevyřazujeme)
    rank = disposition_rank(listing.disposition)
    if rank and rank < disposition_rank(c.min_dispozice):
        return False
    # venkovní prostor jako tvrdá podmínka — jen kvalifikující typy (lodžie se nepočítá)
    if c.vyzaduj_venkovni_prostor and not listing.has_qualifying_outdoor(c.venkovni_typy):
        return False
    return True


def score_listing(listing: Listing, cfg: Config) -> None:
    """Spočítá skóre a lidsky čitelné důvody. Zapíše přímo do listing."""
    c = cfg.search
    score = 50.0
    reasons: list[str] = []

    # --- venkovní prostor (hlavní priorita) ---
    if listing.has_qualifying_outdoor(c.venkovni_typy):
        score += 25
        reasons.append(f"✅ venkovní prostor: {listing.outdoor_label}")
        if listing.terrace or listing.garden:
            score += 5  # terasa/zahrada je bonus navíc
    elif listing.outdoor:  # má jen lodžii — nekvalifikuje se, ale není úplně bez
        score += 4
        reasons.append(f"➖ jen {listing.outdoor_label} (nebere se jako plnohodnotný venkovní prostor)")
    else:
        score -= 5
        reasons.append("➖ bez venkovního prostoru")

    # --- plocha ---
    if listing.area:
        diff = listing.area - c.min_plocha
        if diff >= 0:
            score += min(diff, 15)
            reasons.append(f"✅ plocha {listing.area:.0f} m²")
        else:
            score += max(diff, -15)  # penalizace za menší
            reasons.append(f"➖ menší plocha {listing.area:.0f} m² (cíl {c.min_plocha:.0f}+)")

    # --- cena (levnější = lépe; počítáme CELKOVOU cenu vč. poplatků) ---
    total = listing.total_price
    if total:
        bonus = (c.max_cena - total) / c.max_cena * 15
        score += bonus
        if listing.fees_estimated:
            score -= 2  # odhad = mírná nejistota v ceně
            reasons.append(
                f"💰 ~{total:,} Kč/měs (nájem {listing.price:,} + odhad {listing.fees:,} – {listing.fees_note})".replace(",", " ")
            )
        elif listing.fees == 0:
            reasons.append(f"💰 {total:,} Kč/měs (energie v ceně nájmu)".replace(",", " "))
        elif listing.fees:
            reasons.append(
                f"💰 {total:,} Kč/měs (nájem {listing.price:,} + poplatky {listing.fees:,})".replace(",", " ")
            )
        else:
            # poplatky se nepodařilo zjistit ani odhadnout — reálná cena může být vyšší
            score -= 4
            reasons.append(f"💰 {listing.price:,} Kč/měs ⚠️ + poplatky (neuvedeny)".replace(",", " "))

    # --- kvalita: stav budovy ---
    if listing.building_condition:
        key = listing.building_condition.strip().lower()
        delta = CONDITION_SCORE.get(key)
        if delta:
            score += delta
            sign = "✅" if delta > 0 else "⚠️"
            reasons.append(f"{sign} stav: {listing.building_condition}")

    # --- kvalita: typ stavby ---
    if listing.building_type:
        bt = listing.building_type.strip().lower()
        if "cihl" in bt or "smíšen" in bt or "smisen" in bt:
            score += 4
            reasons.append("✅ cihlová stavba")
        elif "panel" in bt:
            score -= 4
            reasons.append("⚠️ panelová stavba")

    # --- kvalita: klíčová slova v popisu ---
    desc = (listing.description or "").lower()
    for kw, pts in POSITIVE_KW.items():
        if kw in desc:
            score += pts
            reasons.append(f"✅ {kw}")
            break  # jednou stačí, ať to nepřestřelí
    for kw, pts in NEGATIVE_KW.items():
        if kw in desc:
            score -= pts
            reasons.append(f"⛔ {kw}")
            break

    # --- mazlíčci (informace + jemná penalizace; tvrdě filtruje jen config "jen_zakaz"/"vse") ---
    if listing.pets == "zakaz":
        score -= 6
        reasons.append("⛔ inzerát uvádí: bez zvířat")
    elif listing.pets == "nezadouci":
        score -= 3
        reasons.append("🐾 zvířata raději ne (dle inzerátu)")
    elif listing.pets == "po_dohode":
        reasons.append("🐾 mazlíčci po dohodě")
    elif listing.pets == "povoleno":
        reasons.append("🐾 mazlíčci povoleni")

    listing.score = round(score, 1)
    listing.score_reasons = reasons


def process(listings: list[Listing], cfg: Config) -> list[Listing]:
    """Doplní poplatky/příznaky z textu, odfiltruje, oboduje a seřadí (nejlepší první)."""
    out = []
    for l in listings:
        infer_fees(l, cfg)   # doplň poplatky (regex/odhad), než počítáme celkovou cenu
        infer_flags(l)       # doplň rezervaci a mazlíčky z textu, když je LLM nedodalo
        if passes_hard_filter(l, cfg):
            score_listing(l, cfg)
            out.append(l)
    out.sort(key=lambda x: x.score, reverse=True)
    return out
