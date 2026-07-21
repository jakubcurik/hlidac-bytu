"""Načtení a validace konfigurace (config.yaml + .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SearchCriteria:
    mesto: str = "Hradec Králové"
    max_cena: int = 18000            # CELKOVÁ měsíční cena vč. poplatků/energií (pokud jsou známy)
    min_plocha: float = 30
    min_dispozice: str = "1+kk"
    vyzaduj_venkovni_prostor: bool = True
    # Které typy venkovního prostoru se počítají do tvrdého filtru (lodžie záměrně NENÍ).
    venkovni_typy: list = field(default_factory=lambda: ["balkon", "terasa", "zahrada"])
    # Filtr mazlíčků: "jen_zakaz" (vyřadí jen jasný zákaz), "vse" (i nejednoznačné), "vypnuto".
    mazlicci_filtr: str = "jen_zakaz"
    vyloucit_rezervovane: bool = True  # skrýt inzeráty označené jako rezervované/obsazené
    okoli: list = field(default_factory=list)  # povolené okolní obce navíc k městu (default: jen město)


@dataclass
class Config:
    search: SearchCriteria = field(default_factory=SearchCriteria)
    zdroje: dict = field(default_factory=lambda: {
        "sreality": True, "bezrealitky": True, "ulovdomov": True, "idnes": True,
    })
    max_stran_na_zdroj: int = 10
    detail_cache_dny: int = 14
    posilat_telegram: bool = True
    pouzit_llm: bool = True   # zpracovat inzeráty přes LLM (spolehlivé poplatky/energie); bez API klíče se přeskočí
    odhad_poplatku: bool = True  # když poplatky nikde nejsou, nech LLM odhadnout typické zálohy (označí se)
    llm_workers: int = 10     # kolik LLM požadavků paralelně (Tier 1 zvládne víc, první běh je pak rychlejší)

    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


def load_config(path: str | Path | None = None) -> Config:
    """Načte config.yaml (nepovinný — jinak defaulty) a .env se secrets."""
    load_dotenv(ROOT / ".env")

    cfg_path = Path(path) if path else ROOT / "config.yaml"
    raw: dict = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    h = raw.get("hledani", {}) or {}
    default_typy = ["balkon", "terasa", "zahrada"]
    search = SearchCriteria(
        mesto=h.get("mesto", "Hradec Králové"),
        max_cena=int(h.get("max_cena", 18000)),
        min_plocha=float(h.get("min_plocha", 30)),
        min_dispozice=str(h.get("min_dispozice", "1+kk")),
        vyzaduj_venkovni_prostor=bool(h.get("vyzaduj_venkovni_prostor", True)),
        venkovni_typy=[str(x).strip().lower() for x in (h.get("venkovni_typy") or default_typy)],
        mazlicci_filtr=str(h.get("mazlicci_filtr", "jen_zakaz")),
        vyloucit_rezervovane=bool(h.get("vyloucit_rezervovane", True)),
        okoli=list(h.get("okoli", []) or []),
    )

    provoz = raw.get("provoz", {}) or {}
    return Config(
        search=search,
        zdroje=raw.get("zdroje", {}) or {
            "sreality": True, "bezrealitky": True, "ulovdomov": True, "idnes": True,
        },
        max_stran_na_zdroj=int(provoz.get("max_stran_na_zdroj", 10)),
        detail_cache_dny=int(provoz.get("detail_cache_dny", 14)),
        posilat_telegram=bool(provoz.get("posilat_telegram", True)),
        pouzit_llm=bool(provoz.get("pouzit_llm", True)),
        odhad_poplatku=bool(provoz.get("odhad_poplatku", True)),
        llm_workers=int(provoz.get("llm_workers", 10)),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )
