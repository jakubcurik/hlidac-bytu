#!/usr/bin/env python3
"""Hlídač nájemních bytů — spouštěcí soubor.

Použití:
    python main.py                 # jeden běh: scrape + přehled + Telegram
    python main.py --open          # po běhu otevře přehled v prohlížeči
    python main.py --source sreality   # jen jeden portál (ladění)
    python main.py --test-telegram # ověří propojení s Telegramem
    python main.py --config jiny.yaml
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Hlídač nájemních bytů")
    ap.add_argument("--config", help="cesta ke config.yaml", default=None)
    ap.add_argument("--source", help="spustit jen jeden zdroj (sreality/bezrealitky/ulovdomov/idnes)")
    ap.add_argument("--open", action="store_true", help="po běhu otevřít přehled v prohlížeči")
    ap.add_argument("--test-telegram", action="store_true", help="poslat testovací zprávu na Telegram")
    ap.add_argument("--telegram-chatid", action="store_true", help="vypsat chat_id z posledních zpráv botovi (pomůcka při nastavení)")
    ap.add_argument("--test-llm", action="store_true", help="ověřit LLM klíč na ukázkovém inzerátu")
    ap.add_argument("--quiet", action="store_true", help="méně logů")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from hlidac.config import load_config
    from hlidac import notify

    if args.telegram_chatid:
        cfg = load_config(args.config)
        return 0 if notify.print_chat_ids(cfg) else 1

    if args.test_llm:
        load_config(args.config)  # načte .env s klíčem
        from hlidac import llm
        if not llm.available():
            print("❌ Žádný LLM klíč v .env (GEMINI_API_KEY / OPENAI_API_KEY / GROQ_API_KEY).")
            return 1
        print(f"Poskytovatel: {llm.provider_name()}")
        ukazka = ("Cena nájmu 18.000,- Kč/měs plus zálohy 3.500,- Kč/měs za energie. "
                  "Vratná kauce 20.000,- Kč, provize RK 15.000,- Kč. Byt má balkon a sklep.")
        import json as _json
        res = llm.extract(ukazka, title="Pronájem 2+kk", rent=18000)
        print(_json.dumps(res, ensure_ascii=False, indent=2) if res else "❌ Extrakce selhala.")
        return 0 if res else 1

    if args.test_telegram:
        cfg = load_config(args.config)
        ok = notify.send_test(cfg)
        print("✅ Telegram OK — zkontroluj chat." if ok else "❌ Telegram se nepodařilo (viz .env a README).")
        return 0 if ok else 1

    from hlidac.run import run
    result = run(config_path=args.config, only_source=args.source)

    print()
    print(f"  Nalezeno:  {result['total']} bytů")
    print(f"  Nových:    {result['new']}")
    print(f"  Přehled:   {result['dashboard']}")

    if args.open:
        webbrowser.open(Path(result["dashboard"]).resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
