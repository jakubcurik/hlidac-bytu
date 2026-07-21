"""Zpracování inzerátů přes LLM — spolehlivá extrakce a data-enrichment z volného textu.

Proč: portály uvádějí poplatky/energie často jen v textu ("zálohy 3.500,- Kč/měs",
"nájem 15 000 + 5 000 energie", "kauce jeden nájem"), kde regex selhává. LLM to zvládá
spolehlivě a navíc dodá kauci, provizi, venkovní prostor, rezervaci, postoj k mazlíčkům
a chybějící parametry (dispozice, plocha, typ stavby) — vše pro přesnou cenu a scoring.

Poskytovatelé (dle klíče v .env, v tomto pořadí priority):
  GEMINI_API_KEY  -> Google Gemini   (doporučeno — používá structured output / responseSchema)
  OPENAI_API_KEY  -> OpenAI
  GROQ_API_KEY    -> Groq (OpenAI-kompatibilní)

Bez klíče se modul tiše přeskočí — použije se konzervativní regex fallback ve scoring.py.
Výsledky se cachují (ve store), takže se stejný inzerát neposílá do LLM opakovaně.
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

CACHE_DNY = 30          # popisy se nemění, výsledek LLM držíme dlouho
CACHE_VER = "v4"        # verze extrakce — při změně promptu/schématu zvyš, ať se přepočítá

# Povolené typy venkovního prostoru (enum pro LLM i mapování na atributy Listing)
_OUTDOOR_MAP = {"balkon": "balcony", "terasa": "terrace", "lodzie": "loggia", "zahrada": "garden"}

SYSTEM_PROMPT = (
    "Jsi precizní analytik českých realitních inzerátů (pronájmy bytů). Z inzerátu vytáhni "
    "strukturovaná data pro výpočet CELKOVÉ měsíční ceny a pro filtrování. Řiď se přesně:\n"
    "\n"
    "POPLATKY / ENERGIE (nejdůležitější): Dostaneš 'Poznámku k ceně' a 'Text inzerátu'. "
    "Poznámka k ceně je NEJSPOLEHLIVĚJŠÍ zdroj poplatků (např. '+ služby a energie 3.500,-Kč "
    "+ vratná kauce 25.000,-Kč') — čti ji jako první, pak teprve text.\n"
    "• mesicni_poplatky = MĚSÍČNÍ zálohy na energie a služby (teplo, voda, elektřina, plyn, odpad, "
    "úklid, výtah, společné prostory, internet), pokud je v poznámce či textu KONKRÉTNÍ částka. "
    "Zachyť obě slovosledné varianty: 'zálohy na energie 3.500,-' i 'nájem 15 000 + 5 000 za služby'. "
    "Sečti více měsíčních položek dohromady (např. 'služby 3000 + fond oprav 500' = 3500). "
    "Když je částka vázaná na počet osob ('poplatky 3.554 Kč / 2 osoby'), vezmi ji tak, jak je uvedena. "
    "NEZAPOČÍTÁVEJ kauci, jistotu, provizi ani jiné jednorázové platby.\n"
    "• poplatky_v_najmu = true, pokud jsou energie/služby už ZAHRNUTÉ v nájmu ('včetně energií', "
    "'vč. inkasa', 'cena je konečná'). Pak mesicni_poplatky nech null.\n"
    "• energie_zvlast_bez_castky = true, pokud text říká, že energie/služby se platí NAVÍC, ale "
    "NEUVÁDÍ částku (např. '+ energie', 'el. energii si přehlásí nájemník', 'poplatky dle skutečné spotřeby').\n"
    "• poplatky_odhad = tvůj REALISTICKÝ odhad měsíčních záloh v Kč. Vyplň ho VŽDY, když mesicni_poplatky "
    "je null a poplatky_v_najmu je false (tedy i když se energie platí zvlášť, ale bez uvedené částky). "
    "Odhadni podle plochy a dispozice: garsonka/1+kk ~2000–2800, 2+kk/2+1 ~2800–3800, 3+ ~3500–4800 Kč. "
    "Když jsou poplatky v nájmu nebo znáš konkrétní částku, dej null.\n"
    "\n"
    "JEDNORÁZOVÉ PLATBY (umí být relativní k nájmu — přepočítej na Kč pomocí nájmu, který dostaneš):\n"
    "• kauce = vratná kauce/jistota. 'kauce ve výši jednoho nájmu' = nájem, 'dva nájmy' = 2×nájem.\n"
    "• provize = provize RK. 'provize jeden nájem' = nájem. 'bez provize'/'neúčtujeme provizi' = 0.\n"
    "\n"
    "DALŠÍ (pro filtrování a scoring):\n"
    "• venkovni_prostor = pole jen z hodnot 'balkon','terasa','lodzie','zahrada', které JEDNOZNAČNĚ "
    "plynou z textu. Předzahrádka/zahrada = 'zahrada'. Když nic, vrať prázdné pole.\n"
    "• rezervovano = true, pokud je inzerát označen jako rezervovaný, obsazený, pronajatý, "
    "'REZERVOVÁNO', 'již pronajato' apod.\n"
    "• mazlicci = postoj k domácím zvířatům. Projdi text pečlivě a zachyť KAŽDOU zmínku o zvířatech "
    "či mazlíčcích, i nepřímou. Rozlišuj přesně:\n"
    "  - 'zakaz' = výslovný zákaz či podmínka bez zvířat: 'bez domácích mazlíčků', 'zvířata nejsou "
    "povolena', 'zákaz chovu zvířat', 'pouze nájemníci bez zvířat', 'nevhodné pro zvířata'.\n"
    "  - 'nezadouci' = preference proti zvířatům, ale ne výslovný zákaz: 'ideálně bez mazlíčků', "
    "'raději bez zvířat', 'upřednostníme zájemce bez zvířat', 'spíše nevhodné pro zvířata'.\n"
    "  - 'po_dohode' = podmíněná možnost: 'zvíře po dohodě', 'menší pes možný', 'dle domluvy "
    "s majitelem', 'kočka ano, pes ne'.\n"
    "  - 'povoleno' = výslovně dovoleno/vítáno: 'mazlíčci vítáni', 'zvířata povolena'.\n"
    "  - 'neuvedeno' = v textu není o zvířatech ani slovo.\n"
    "  POZOR: 'ideálně/raději/přednostně bez zvířat' je 'nezadouci', NE 'po_dohode' ani 'zakaz'.\n"
    "• dispozice = např. '2+kk','3+1','garsoniera' (jen když z textu plyne). plocha_m2 = užitná "
    "plocha v m² (číslo). typ_stavby = 'cihlová'/'panelová'/'smíšená'/… (jen když plyne).\n"
    "• shrnuti = max 14 slov, česky, věcně, to podstatné pro rozhodování.\n"
    "\n"
    "Čísla jako '3.500,-' nebo '3 500 Kč' ber jako 3500. Nic si nevymýšlej (kromě výslovně "
    "požadovaného odhadu poplatky_odhad). Když údaj chybí, dej null / 'neuvedeno' / prázdné pole."
)

# JSON schema pro Gemini structured output (typy velkými písmeny dle Gemini REST).
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "mesicni_poplatky": {"type": "INTEGER", "nullable": True},
        "poplatky_v_najmu": {"type": "BOOLEAN"},
        "energie_zvlast_bez_castky": {"type": "BOOLEAN"},
        "poplatky_odhad": {"type": "INTEGER", "nullable": True},
        "kauce": {"type": "INTEGER", "nullable": True},
        "provize": {"type": "INTEGER", "nullable": True},
        "venkovni_prostor": {
            "type": "ARRAY",
            "items": {"type": "STRING", "enum": ["balkon", "terasa", "lodzie", "zahrada"]},
        },
        "rezervovano": {"type": "BOOLEAN"},
        "mazlicci": {"type": "STRING",
                     "enum": ["povoleno", "zakaz", "nezadouci", "po_dohode", "neuvedeno"]},
        "dispozice": {"type": "STRING", "nullable": True},
        "plocha_m2": {"type": "NUMBER", "nullable": True},
        "typ_stavby": {"type": "STRING", "nullable": True},
        "shrnuti": {"type": "STRING"},
    },
    "required": ["poplatky_v_najmu", "energie_zvlast_bez_castky", "rezervovano",
                 "mazlicci", "venkovni_prostor", "shrnuti"],
    "propertyOrdering": [
        "mesicni_poplatky", "poplatky_v_najmu", "energie_zvlast_bez_castky", "poplatky_odhad",
        "kauce", "provize", "venkovni_prostor", "rezervovano", "mazlicci",
        "dispozice", "plocha_m2", "typ_stavby", "shrnuti",
    ],
}


def _provider() -> dict | None:
    """Vybere poskytovatele podle dostupného klíče v prostředí. None = žádný klíč."""
    model_override = os.getenv("LLM_MODEL")
    if os.getenv("GEMINI_API_KEY"):
        return {
            "kind": "gemini",
            "key": os.getenv("GEMINI_API_KEY"),
            "model": model_override or "gemini-3.6-flash",
            # alias 'latest' vždy míří na aktuální flash model — záchrana, kdyby primární dal 404
            "fallback_model": "gemini-flash-latest",
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


def _gemini_call(prov: dict, model: str, user_text: str) -> httpx.Response:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={prov['key']}"
    )
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }
    return httpx.post(url, json=body, timeout=60.0)


def _call(prov: dict, user_text: str) -> str | None:
    """Zavolá LLM a vrátí surový textový výstup (JSON). Retry na 429/503, fallback modelu na 404."""
    model = prov["model"]
    for attempt in range(4):
        try:
            if prov["kind"] == "gemini":
                r = _gemini_call(prov, model, user_text)
                # Neznámý název modelu -> jednorázově přepni na ověřený alias a zkus znovu.
                if r.status_code == 404 and model != prov.get("fallback_model"):
                    log.warning("Gemini model '%s' nedostupný (404), přepínám na '%s'.",
                                model, prov["fallback_model"])
                    model = prov["fallback_model"]
                    continue
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
                    timeout=60.0,
                )
            if r.status_code in (429, 500, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))  # backoff při zahlcení/přechodné chybě
                continue
            r.raise_for_status()
            if prov["kind"] == "gemini":
                cand = r.json()["candidates"][0]
                return cand["content"]["parts"][0]["text"]
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < 3:
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
        i, j = raw.find("{"), raw.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(raw[i:j + 1])
            except json.JSONDecodeError:
                return None
    return None


def extract(description: str, title: str = "", rent: int | None = None,
            price_note: str = "") -> dict | None:
    """Vytáhne strukturovaná data z popisu + poznámky k ceně. None, když není klíč / volání selže."""
    prov = _provider()
    if not prov or not (description or price_note):
        return None
    user = (
        f"Nájem (základní, měsíčně): {rent if rent else 'neuvedeno'} Kč.\n"
        f"Poznámka k ceně (z portálu): {price_note.strip() or '—'}\n"
        f"Název inzerátu: {title}\n"
        f"Text inzerátu:\n{description or '—'}"
    )
    return _parse_json(_call(prov, user))


def _as_int(v) -> int | None:
    """Bezpečný převod na kladné celé číslo (0 povoleno). Jinak None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and v >= 0:
        return int(v)
    if isinstance(v, str):
        digits = "".join(ch for ch in v if ch.isdigit())
        return int(digits) if digits else None
    return None


def _apply(listing: Listing, data: dict, allow_estimate: bool = True) -> None:
    """Aplikuje výsledek LLM na inzerát (poplatky, jednorázové platby, prostor, rezervace, mazlíčci)."""
    # --- poplatky / celková cena ---
    # Strukturované pole z portálu (např. Sreality "Měsíční výdaje") má VŽDY přednost —
    # LLM smí poplatky doplnit jen tam, kde portál nic nedal. Odhad NIKDY nepřebíjí známou částku.
    if listing.fees is None:
        real = _as_int(data.get("mesicni_poplatky"))
        est = _as_int(data.get("poplatky_odhad"))
        if data.get("poplatky_v_najmu") is True:
            listing.fees = 0
            listing.fees_estimated = False
            listing.fees_note = "energie/služby v ceně nájmu"
        elif real:
            listing.fees = real
            listing.fees_estimated = False
            listing.fees_note = "z inzerátu"
        elif allow_estimate and est:
            listing.fees = est
            listing.fees_estimated = True
            note = "odhad (v inzerátu neuvedeno)"
            if data.get("energie_zvlast_bez_castky") is True:
                note = "odhad (energie zvlášť, částka neuvedena)"
            listing.fees_note = note
        # jinak fees necháme (může doplnit regex fallback ve scoring.infer_fees)

    # --- jednorázové platby + shrnutí ---
    kauce = _as_int(data.get("kauce"))
    if kauce is not None:
        listing.deposit = kauce
    provize = _as_int(data.get("provize"))
    if provize is not None:
        listing.commission = provize
    if data.get("shrnuti"):
        listing.summary = str(data["shrnuti"])[:180]

    # --- rezervace / mazlíčci ---
    if data.get("rezervovano") is True:
        listing.reserved = True
    mazl = data.get("mazlicci")
    if mazl in ("povoleno", "zakaz", "nezadouci", "po_dohode"):
        listing.pets = mazl

    # --- venkovní prostor: jen PŘIDÁVÁ, nikdy nemaže signál z portálu ---
    for v in (data.get("venkovni_prostor") or []):
        attr = _OUTDOOR_MAP.get(str(v).strip().lower())
        if attr:
            setattr(listing, attr, True)

    # --- doplnění chybějících parametrů (nikdy nepřepisuje spolehlivá data z portálu) ---
    if not listing.disposition and data.get("dispozice"):
        from .models import normalize_disposition
        listing.disposition = normalize_disposition(str(data["dispozice"]))
    if not listing.area:
        plocha = data.get("plocha_m2")
        if isinstance(plocha, (int, float)) and plocha > 0:
            listing.area = float(plocha)
    if not listing.building_type and data.get("typ_stavby"):
        listing.building_type = str(data["typ_stavby"])


def _cache_key(l: Listing) -> str:
    return f"llm:{CACHE_VER}:{l.source}:{l.source_id}"


def enrich(listing: Listing, store: Store, cfg: Config) -> bool:
    """Doplní jeden inzerát daty z LLM (s cache). Vrací True, pokud jsou data k dispozici."""
    if not (listing.description or listing.price_note):
        return False
    ck = _cache_key(listing)
    data = store.cache_get(ck, max_age_days=CACHE_DNY)
    if data is None:
        data = extract(listing.description, listing.title, listing.price, listing.price_note)
        if data is None:
            return False
        store.cache_set(ck, data)
    _apply(listing, data, allow_estimate=cfg.odhad_poplatku)
    return True


def enrich_many(listings: list[Listing], store: Store, cfg: Config, workers: int | None = None) -> int:
    """Hromadné zpracování přes LLM. Síťová volání běží PARALELNĚ (rychlý první běh),
    čtení/zápis cache i aplikace dat běží v hlavním vlákně (SQLite není thread-safe).
    Vrací počet obohacených inzerátů."""
    if not available():
        return 0
    workers = workers or cfg.llm_workers
    todo: list[tuple[Listing, str]] = []
    applied = 0

    # 1) cache hity vyřídíme rovnou (bez volání LLM)
    for l in listings:
        if not (l.description or l.price_note):
            continue
        ck = _cache_key(l)
        cached = store.cache_get(ck, max_age_days=CACHE_DNY)
        if cached is not None:
            _apply(l, cached, allow_estimate=cfg.odhad_poplatku)
            applied += 1
        else:
            todo.append((l, ck))

    if not todo:
        return applied

    # 2) chybějící stáhneme paralelně (jen síť, žádná DB v pracovních vláknech)
    def _fetch(item: tuple[Listing, str]):
        l, ck = item
        return item, extract(l.description, l.title, l.price, l.price_note)

    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for (l, ck), data in ex.map(_fetch, todo):
            if data is not None:
                store.cache_set(ck, data)  # zápis do DB v hlavním vlákně
                _apply(l, data, allow_estimate=cfg.odhad_poplatku)
                applied += 1
            else:
                failed += 1
    if failed:
        log.warning("LLM: %d inzerátů se nepodařilo zpracovat (použije se odhad/regex z popisu).", failed)
    return applied
