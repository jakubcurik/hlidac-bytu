"""Telegram notifikace o nových bytech.

Používá přímo Telegram Bot API přes httpx (žádná další závislost).
Chování:
  - první běh (prázdná databáze) = pošle jen JEDEN souhrn (ať nezahltí desítkami zpráv),
  - další běhy = pošle kartu ke každému NOVÉMU bytu (s fotkou), max do limitu.
"""
from __future__ import annotations

import html
import logging

import httpx

from .config import Config
from .models import Listing

log = logging.getLogger("hlidac.notify")

API = "https://api.telegram.org/bot{token}/{method}"
MAX_INDIVIDUAL = 15  # kolik nových bytů max poslat jako jednotlivé karty za běh


def _post(cfg: Config, method: str, data: dict) -> bool:
    url = API.format(token=cfg.telegram_token, method=method)
    try:
        r = httpx.post(url, data=data, timeout=20.0)
        if r.status_code != 200:
            log.warning("Telegram %s -> %s: %s", method, r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("Telegram %s chyba: %s", method, e)
        return False


def send_message(cfg: Config, text: str) -> bool:
    return _post(cfg, "sendMessage", {
        "chat_id": cfg.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })


def _caption(l: Listing) -> str:
    e = html.escape
    lines = [f"🏡 <b>{e(l.disposition or 'byt')}"
             + (f", {int(l.area)} m²" if l.area else "") + "</b>"]
    if l.fees_known:
        total = f"{l.total_price:,}".replace(",", " ")
        zdroj = "odhad vč. poplatků" if l.fees_estimated else "vč. poplatků"
        rent = f"{l.price:,}".replace(",", " ")
        fee = f"{l.fees:,}".replace(",", " ")
        lines.append(f"💰 <b>{total} Kč/měs</b> ({zdroj}: {rent} + {fee})")
    else:
        price = f"{l.price:,}".replace(",", " ") if l.price else "?"
        lines.append(f"💰 <b>{price} Kč/měs</b> ⚠️ + poplatky (neuvedeny)")
    tags = []
    if l.outdoor:
        tags.append("🌿 " + l.outdoor_label)
    if l.building_type:
        tags.append(e(l.building_type))
    if l.building_condition:
        tags.append(e(l.building_condition))
    if tags:
        lines.append(" · ".join(tags))
    if l.address:
        lines.append("📍 " + e(l.address))
    lines.append(f"⭐ skóre {l.score}")
    lines.append(f'<a href="{e(l.url)}">Zobrazit inzerát →</a>')
    return "\n".join(lines)


def send_listing(cfg: Config, l: Listing) -> bool:
    caption = _caption(l)
    if l.image:
        ok = _post(cfg, "sendPhoto", {
            "chat_id": cfg.telegram_chat_id,
            "photo": l.image,
            "caption": caption,
            "parse_mode": "HTML",
        })
        if ok:
            return True
        # fallback na textovou zprávu, pokud fotka selže (např. nedostupná URL)
    return send_message(cfg, caption)


def notify_new(cfg: Config, new_listings: list[Listing], first_run: bool) -> list[str]:
    """Pošle notifikace a vrátí klíče bytů, které se podařilo oznámit."""
    if not cfg.posilat_telegram:
        log.info("Telegram vypnutý v configu — přeskočeno.")
        return []
    if not cfg.telegram_ready:
        log.warning("Telegram není nastaven (chybí token / chat_id v .env) — přeskočeno.")
        return []
    if not new_listings:
        return []

    new_listings = sorted(new_listings, key=lambda x: x.score, reverse=True)
    notified: list[str] = []

    if first_run:
        # první běh: jen souhrn + top 3 řádky, ať to nespamuje
        top = new_listings[:3]
        lines = [f"🏡 <b>Hlídač bytů spuštěn!</b>",
                 f"Našel jsem <b>{len(new_listings)}</b> vyhovujících bytů v {html.escape(cfg.search.mesto)}.",
                 "", "Nejlepší shody:"]
        for l in top:
            tp = f"{l.total_price:,}".replace(",", " ") if l.total_price else "?"
            suffix = "" if l.fees_known else " + popl."
            extra = f" · 🌿 {l.outdoor_label}" if l.outdoor else ""
            lines.append(f"• {l.disposition} {int(l.area) if l.area else '?'} m² — {tp} Kč{suffix}{extra}")
        lines.append("")
        lines.append("Kompletní přehled máš v souboru <code>output/index.html</code>.")
        lines.append("Od teď ti budu hlásit každý nový byt. 💜")
        if send_message(cfg, "\n".join(lines)):
            notified = [l.key for l in new_listings]  # označíme vše za oznámené
        return notified

    # běžný běh: jednotlivé karty
    for l in new_listings[:MAX_INDIVIDUAL]:
        if send_listing(cfg, l):
            notified.append(l.key)
    extra = len(new_listings) - MAX_INDIVIDUAL
    if extra > 0:
        send_message(cfg, f"…a dalších <b>{extra}</b> nových bytů — viz přehled <code>output/index.html</code>.")
        # zbylé taky označíme jako oznámené (jsou v přehledu)
        notified += [l.key for l in new_listings[MAX_INDIVIDUAL:]]
    return notified


def send_test(cfg: Config) -> bool:
    if not cfg.telegram_ready:
        log.error("Chybí TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID v .env")
        return False
    return send_message(cfg, "✅ Test: hlídač nájemních bytů je propojený s Telegramem.")


def print_chat_ids(cfg: Config) -> bool:
    """Pomocník při nastavení: vypíše chat_id z posledních zpráv botovi.
    Návod: v Telegramu napiš svému botovi libovolnou zprávu a pak spusť tenhle příkaz."""
    if not cfg.telegram_token:
        print("❌ Chybí TELEGRAM_BOT_TOKEN v .env — nejdřív vytvoř bota (viz README).")
        return False
    url = API.format(token=cfg.telegram_token, method="getUpdates")
    try:
        r = httpx.get(url, timeout=20.0)
        updates = r.json().get("result", [])
    except Exception as e:
        print("❌ Chyba při volání Telegramu:", e)
        return False
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            name = chat.get("title") or " ".join(
                p for p in [chat.get("first_name"), chat.get("last_name")] if p
            ) or chat.get("username") or ""
            chats[chat["id"]] = name
    if not chats:
        print("ℹ️  Zatím žádné zprávy. Napiš svému botovi v Telegramu libovolnou zprávu")
        print("    (třeba 'ahoj') a spusť příkaz znovu.")
        return False
    print("Nalezená chat_id (vlož do .env jako TELEGRAM_CHAT_ID):\n")
    for cid, name in chats.items():
        print(f"    {cid}   {name}")
    return True
