"""Normalizovaný datový model inzerátu, sdílený napříč všemi portály."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def now_iso() -> str:
    """Aktuální čas v ISO formátu (UTC). Kvůli testovatelnosti na jednom místě."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def city_key(s: str | None) -> str:
    """Normalizace názvu obce pro porovnání: bez diakritiky, malá písmena, bez mezer navíc.
    'Hradec Králové' -> 'hradec kralove'."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", ascii_str).strip()


# Poplatky/služby/energie v textu, např. "poplatky za služby 3 500 Kč", "energie cca 2500,-"
_FEE_RE = re.compile(
    r"(?:poplat\w*|služ\w*|sluz\w*|energi\w*|inkas\w*|zálo\w*|zalo\w*)"
    r"[^0-9]{0,30}?(\d[\d\s]{2,6})\s*(?:kč|kc|czk|,-)",
    re.I,
)


def parse_fees_from_text(text: str | None, rent: int | None = None) -> int | None:
    """Konzervativně vytáhne měsíční poplatky/energie z volného textu popisu.
    Vrací číslo jen v rozumném rozsahu (300–15000) a různé od samotného nájmu."""
    if not text:
        return None
    for m in _FEE_RE.finditer(text):
        num = int(re.sub(r"\s", "", m.group(1)))
        if 300 <= num <= 15000 and (rent is None or num != rent):
            return num
    return None


# --- Dispozice ------------------------------------------------------------

# Číselné pořadí dispozic — kvůli filtru "minimálně 1+kk".
# Konvence: počet pokojů * 10, +0 pro "kk", +1 pro plnou kuchyň ("+1").
_DISPOSITION_RANK = {
    "pokoj": 1,
    "garsoniera": 5,
    "garsonka": 5,
    "1+kk": 10, "1+1": 11,
    "2+kk": 20, "2+1": 21,
    "3+kk": 30, "3+1": 31,
    "4+kk": 40, "4+1": 41,
    "5+kk": 50, "5+1": 51,
    "6+kk": 60, "6+1": 61,
    "7+kk": 70, "7+1": 71,
    "atypicky": 15,
}


def normalize_disposition(raw: str | None) -> str:
    """Sjednotí zápis dispozice: '1 kk', '1kk', '1 + kk' -> '1+kk'."""
    if not raw:
        return ""
    s = raw.strip().lower()
    s = s.replace("dispozice", "").strip()
    # sjednocení diakritiky u atyp./garsonky
    s = s.replace("atypické", "atypicky").replace("atypický", "atypicky")
    s = s.replace("garsoniéra", "garsoniera").replace("garsónka", "garsonka")
    # "3+kk", "3 + kk", "3kk", "3 kk" -> "3+kk"
    m = re.search(r"(\d)\s*\+?\s*(kk|1)\b", s)
    if m:
        return f"{m.group(1)}+{'kk' if m.group(2) == 'kk' else '1'}"
    if "garson" in s:
        return "garsoniera"
    if "pokoj" in s:
        return "pokoj"
    if "atypick" in s:
        return "atypicky"
    return s


def disposition_rank(disp: str | None) -> int:
    """Vrátí číselné pořadí dispozice (0 = neznámá)."""
    return _DISPOSITION_RANK.get(normalize_disposition(disp), 0)


def parse_area(text: str | None) -> float | None:
    """Vytáhne plochu v m² z textu typu 'Pronájem bytu 2+kk 48 m²'."""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m(?:²|2|\^?2)?\b", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


# --- Inzerát --------------------------------------------------------------

@dataclass
class Listing:
    """Jeden nájemní byt sjednotně napříč portály."""

    source: str            # "sreality" | "bezrealitky" | "ulovdomov" | "idnes"
    source_id: str         # jedinečné ID v rámci portálu
    url: str
    title: str = ""

    price: int | None = None        # měsíční nájem v Kč (základní, bez služeb pokud známo)
    price_note: str = ""            # poznámka k ceně ("+ služby", "vč. energií", …)
    fees: int | None = None         # služby/energie/poplatky zvlášť, pokud jsou známy
    fees_estimated: bool = False    # True = poplatky odhadnuté z popisu, ne přímo z portálu
    deposit: int | None = None      # vratná kauce (jednorázová) — typicky z LLM
    commission: int | None = None   # provize RK (jednorázová) — typicky z LLM
    summary: str = ""               # krátké shrnutí inzerátu (z LLM)

    disposition: str = ""           # normalizováno na "2+kk"
    area: float | None = None       # užitná plocha v m²

    address: str = ""
    city: str = ""
    district: str = ""
    lat: float | None = None
    lon: float | None = None

    images: list[str] = field(default_factory=list)
    description: str = ""

    # Vlastnosti (venkovní prostor = hlavní priorita)
    balcony: bool = False
    terrace: bool = False
    loggia: bool = False
    garden: bool = False

    furnished: str | None = None        # stav vybavení
    building_type: str | None = None    # Cihlová / Panelová / …
    building_condition: str | None = None  # Novostavba / Dobrý / Před rekonstrukcí / …
    floor: int | None = None
    elevator: bool | None = None
    available_from: str | None = None

    # Doplněno pipeline později
    first_seen: str = ""
    last_seen: str = ""
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def outdoor(self) -> bool:
        """Má byt jakýkoli venkovní prostor?"""
        return bool(self.balcony or self.terrace or self.loggia or self.garden)

    @property
    def outdoor_label(self) -> str:
        parts = []
        if self.balcony:
            parts.append("balkon")
        if self.terrace:
            parts.append("terasa")
        if self.loggia:
            parts.append("lodžie")
        if self.garden:
            parts.append("zahrada")
        return ", ".join(parts)

    @property
    def fees_known(self) -> bool:
        return self.fees is not None

    @property
    def total_price(self) -> int | None:
        """Celková měsíční cena = nájem + poplatky/energie (pokud jsou známy)."""
        if self.price is None:
            return None
        return self.price + (self.fees or 0)

    @property
    def price_per_m2(self) -> int | None:
        # počítáme z celkové ceny — to je to, co se reálně platí za m²
        tp = self.total_price
        if tp and self.area:
            return round(tp / self.area)
        return None

    @property
    def image(self) -> str | None:
        return self.images[0] if self.images else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Listing":
        # bezpečně ignoruj případná pole navíc
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
