"""Společné rozhraní scraperů + sdílené pomocné funkce."""
from __future__ import annotations

import json
import re
import unicodedata
from abc import ABC, abstractmethod

from ..config import Config
from ..http import Http
from ..models import Listing
from ..store import Store


class Scraper(ABC):
    """Každý portál implementuje jeden Scraper vracející normalizované Listing objekty."""

    name: str = "base"

    @abstractmethod
    def fetch(self, cfg: Config, http: Http, store: Store) -> list[Listing]:
        """Stáhne a vrátí inzeráty (už s doplněným detailem, pokud to jde)."""
        raise NotImplementedError


def slugify(text: str) -> str:
    """'Hradec Králové' -> 'hradec-kralove' (bez diakritiky, mezery na pomlčky)."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    ascii_str = re.sub(r"[^a-z0-9]+", "-", ascii_str)
    return ascii_str.strip("-")


_NEXT_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def extract_next_data(html: str) -> dict | None:
    """Vytáhne a naparsuje __NEXT_DATA__ JSON z Next.js stránky (Sreality, Ulovdomov)."""
    m = _NEXT_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
