"""Volitelné zpracování inzerátů přes LLM — spolehlivá extrakce z volného textu popisu.

Proč: portály uvádějí poplatky/energie často jen v textu ("zálohy 3.500,- Kč/měs"),
kde regex selhává (tečka jako oddělovač, různé formulace, odlišení od kauce/provize).
LLM to zvládá spolehlivě a navíc dodá kauci, provizi a krátké shrnutí.

Podporované poskytovatele (dle klíče v .env, v tomto pořadí priority):
  GEMINI_API_KEY  -> Google Gemini            (doporučeno, štědrý free tier)
  OPENAI_API_KEY  -> OpenAI
  GROQ_API_KEY    -> Groq (rychlý, levný)

Bez klíče se modul tiše přeskočí — použije se konzervativní regex fallback ve scoring.py.
Výsledky se cachují (v store), takže se stejný inzerát neposílá do LLM opakovaně.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .config import Config
from .models import Listing
from .store import Store

log = logging.getLogger("hlidac.llm")

CACHE_DNY = 30  # popisy se nemění, výsledek LLM držíme dlouho

SYSTEM_PROMPT = (
    "Jsi přesný extrakční nástroj pro české realitní inzeráty (pronájmy bytů). "
    "Z textu inzerátu vytáhni strukturovaná data a vrať POUZE validní JSON (bez markdownu, "
    "bez komentářů) přesně v tomto tvaru:\n"
    "{\n"
    '  "mesicni_poplatky": <celé číslo v Kč nebo null>,   // MĚSÍČNÍ zálohy/poplatky za energie a služby '
    "(teplo, voda, elektřina, odpad, úklid, společné prostory). NE kauce, NE provize, NE jednorázové platby.\n"
    '  "poplatky_v_najmu": <true|false>,                   // true, pokud jsou energie/služby už zahrnuté v nájmu\n'
    '  "kauce": <celé číslo nebo null>,                     // vratná kauce/jistota (jednorázová)\n'
    '  "provize": <celé číslo nebo null>,                  // provize RK (jednorázová)\n'
    '  "venkovni_prostor": <pole z hodnot "balkon","terasa","lodzie","zahrada">,  // co jednoznačně plyne z textu\n'
    '  "shrnuti": "<max 12 slov, česky, věcně>"\n'
    "}\n"
    'Čísla jako "3.500,-" nebo "3 500 Kč" ber jako 3500. Když údaj chybí, dej null. Nic si nevymýšlej.'
)


def _provider() -> dict | None:
    """Vybere poskytovatele podle dostupného klíče v prostředí. None = žádný klíč."""
    model_override = os.getenv("LLM_MODEL")
    if os.getenv("GEMINI_API_KEY"):
        return {
            "kind": "gemini",
            "key": os.getenv("GEMINI_API_KEY"),
            # alias 'latest' vždy míří na aktuální flash model (nezastará a je všude dostupný)
            "model": model_override or "gemini-flash-latest",
        }
    if os.getenv("OPENAI_API_KEY"):
        return {
            "kind": "openai",
            "key": os.getenv("OPENAI_API_KEY"),
            "base": "https://api.openai.com/v1",
            "model": model_override or "gpt-4o-mini",
        }
    if os.getenv("GROQ_API_KEY"):
        return {
            "kind": "openai",  # Groq je OpenAI-kompatibilní
            "key": os.getenv("GROQ_API_KEY"),
            "base": "https://api.groq.com/openai/v1",
            "model": model_override or "llama-3.3-70b-versatile",
        }
    return None


def available() -> bool:
    return _provider() is not None


def provider_name() -> str:
    p = _provider()
    if not p:
        return "žádný (chybí API klíč)"
    return f"{p['kind']} / {p['model']}"


def _call(prov: dict, user_text: str) -> str | None:
    """Zavolá LLM a vrátí surový textový výstup (JSON). Retry na rate-limit (429) a 503."""
    for attempt in range(3):
        try:
            if prov["kind"] == "gemini":
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{prov['model']}:generateContent?key={prov['key']}"
                )
                body = {
                    "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": user_text}]}],
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                }
                r = httpx.post(url, json=body, timeout=45.0)
            else:  # openai-kompatibilní (OpenAI, Groq)
                r = httpx.post(
                    f"{prov['base']}/chat/completions",
                    headers={"Authorization": f"Bearer {prov['key']}"},
                    json={
                        "model": prov["model"],
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_text},
                        ],
                    },
                    timeout=45.0,
                )
            if r.status_code in (429, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))  # krátký backoff při zahlcení
                continue
            r.raise_for_status()
            if prov["kind"] == "gemini":
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            log.warning("LLM volání selhalo (%s): %s", prov.get("kind"), e)
            return None
    return None


def _parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):  # kdyby model přidal markdown fence
        raw = raw.strip("`")
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # zkus vyseknout {...}
        i, j = raw.find("{"), raw.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(raw[i:j + 1])
            except json.JSONDecodeError:
                return None
    return None


def extract(description: str, title: str = "", rent: int | None = None) -> dict | None:
    """Vytáhne strukturovaná data z popisu. None, když není klíč / volání selže."""
    prov = _provider()
    if not prov or not description:
        return None
    user = f"Nájem: {rent if rent else 'neuvedeno'} Kč/měs.\nNázev: {title}\nPopis inzerátu:\n{description}"
    return _parse_json(_call(prov, user))


_OUTDOOR_MAP = {"balkon": "balcony", "terasa": "terrace", "lodzie": "loggia",
                "lodžie": "loggia", "zahrada": "garden"}


def _apply(listing: Listing, data: dict) -> None:
    """Aplikuje výsledek LLM na inzerát (poplatky, kauce, provize, shrnutí, venkovní prostor)."""
    # poplatky / celková cena
    if data.get("poplatky_v_najmu") is True:
        listing.fees = 0  # energie/služby jsou v nájmu -> žádné navíc
        listing.fees_estimated = False
    else:
        mf = data.get("mesicni_poplatky")
        if isinstance(mf, (int, float)) and mf:
            listing.fees = int(mf)
            listing.fees_estimated = False  # LLM čte reálnou formulaci, bereme jako spolehlivé

    # jednorázové platby + shrnutí
    if isinstance(data.get("kauce"), (int, float)):
        listing.deposit = int(data["kauce"])
    if isinstance(data.get("provize"), (int, float)):
        listing.commission = int(data["provize"])
    if data.get("shrnuti"):
        listing.summary = str(data["shrnuti"])[:160]

    # venkovní prostor: jen PŘIDÁVÁ, nikdy nemaže signál z portálu
    for v in (data.get("venkovni_prostor") or []):
        attr = _OUTDOOR_MAP.get(str(v).strip().lower())
        if attr:
            setattr(listing, attr, True)


def enrich(listing: Listing, store: Store, cfg: Config) -> bool:
    """Doplní jeden inzerát daty z LLM (s cache). Vrací True, pokud jsou data k dispozici."""
    if not listing.description:
        return False
    cache_key = f"llm:{listing.source}:{listing.source_id}"
    data = store.cache_get(cache_key, max_age_days=CACHE_DNY)
    if data is None:
        data = extract(listing.description, listing.title, listing.price)
        if data is None:
            return False
        store.cache_set(cache_key, data)
    _apply(listing, data)
    return True


def enrich_many(listings: list[Listing], store: Store, cfg: Config, workers: int = 6) -> int:
    """Hromadné zpracování přes LLM. Síťová volání běží PARALELNĚ (rychlý první běh),
    čtení/zápis cache i aplikace dat běží v hlavním vlákně (SQLite není thread-safe).
    Vrací počet obohacených inzerátů."""
    if not available():
        return 0
    todo: list[tuple[Listing, str]] = []
    applied = 0

    # 1) cache hity vyřídíme rovnou (bez volání LLM)
    for l in listings:
        if not l.description:
            continue
        ck = f"llm:{l.source}:{l.source_id}"
        cached = store.cache_get(ck, max_age_days=CACHE_DNY)
        if cached is not None:
            _apply(l, cached)
            applied += 1
        else:
            todo.append((l, ck))

    if not todo:
        return applied

    # 2) chybějící stáhneme paralelně (jen síť, žádná DB v pracovních vláknech)
    def _fetch(item: tuple[Listing, str]):
        l, ck = item
        return item, extract(l.description, l.title, l.price)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for (l, ck), data in ex.map(_fetch, todo):
            if data is not None:
                store.cache_set(ck, data)  # zápis do DB v hlavním vlákně
                _apply(l, data)
                applied += 1
    return applied
