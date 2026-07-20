# 🏡 Hlídač nájemních bytů

Automatický hlídač nájemních bytů, který za tebe pravidelně prochází české realitní
portály, vybere byty podle tvých kritérií, seřadí je podle toho, jak se ti hodí, a **na
nový vyhovující byt tě upozorní na Telegramu**. Zároveň vygeneruje přehledný **HTML dashboard**.

Prohledává: **Sreality**, **Bezrealitky**, **Ulovdomov** a **iDNES Reality**.

Nastavené je to na hledání v **Hradci Králové do 18 000 Kč/měs**, od 1+kk, ideálně 30 m²+,
s důrazem na **balkon / terasu / lodžii / zahradu**. Vše se dá změnit v `config.yaml`.

---

## Jak to funguje (v kostce)

1. Stáhne aktuální nabídky ze čtyř portálů (přes jejich veřejná data — žádné triky, žádné přihlašování).
2. Nechá **jen byty v hledaném městě** (ne v celém okrese — Chlumec, Nový Bydžov apod. se vyřadí).
3. **LLM** (Gemini) přečte popisy a spolehlivě z nich vytáhne **měsíční poplatky/energie**, kauci, provizi a krátké shrnutí.
4. Vyřadí to, co neodpovídá (drahé podle **celkové ceny = nájem + poplatky**, malá dispozice…), a zbytek **oboduje**
   (venkovní prostor = největší plus, dál plocha, celková cena, cihla vs. panel, stav).
5. Uloží si, co už vidělo (do `state.db`), takže pozná, co je **nové**.
6. Vygeneruje dashboard `output/index.html` a na nové byty pošle **Telegram** notifikaci.

Pustíš to jednou denně/hodinu (ručně nebo automaticky) a máš klid.

> **Celková cena:** filtr i řazení počítají s nájmem **plus** poplatky/energie. Kde portál poplatky
> neuvádí a nejde je vyčíst ani z popisu, byt se označí „poplatky neuvedeny" (reálná cena může být vyšší).

---

## ⚡ Rychlý start (Mac)

Otevři aplikaci **Terminál** a postupuj krok za krokem.

### 1. Ověř, že máš Python 3

```bash
python3 --version
```

Pokud vypíše `Python 3.10` nebo vyšší, super. Když ne, nainstaluj si ho z
[python.org](https://www.python.org/downloads/) (velké tlačítko „Download").

### 2. Stáhni projekt

```bash
git clone https://github.com/<tvuj-ucet>/najemni-byty.git
cd najemni-byty
```

(Nebo si repo stáhni jako ZIP přes tlačítko **Code → Download ZIP** a rozbal ho.)

### 3. Nainstaluj závislosti

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> `.venv` je „virtuální prostředí" — izolovaná instalace knihoven jen pro tenhle projekt.
> Při každém dalším spuštění v novém terminálu stačí zopakovat `source .venv/bin/activate`.
> (Spouštěč `./run.sh` to dělá za tebe automaticky — viz plánování níže.)

### 4. Nastav si hledání

```bash
cp config.example.yaml config.yaml
```

Otevři `config.yaml` v libovolném editoru a uprav si město, cenu, plochu, dispozici…
(Soubor je okomentovaný. Když nic nezměníš, hledá Hradec Králové do 18 000 Kč.)

### 5. Nastav Telegram (viz podrobný návod níže)

```bash
cp .env.example .env
```

Do `.env` vlož `TELEGRAM_BOT_TOKEN` a `TELEGRAM_CHAT_ID`. Návod je [o kus níž](#-telegram-krok-za-krokem).

> Nechceš Telegram? Nastav v `config.yaml` `posilat_telegram: false` a používej jen dashboard.

### 6. Spusť to!

```bash
python3 main.py --open
```

`--open` po doběhnutí otevře přehled v prohlížeči. Hotovo 🎉

---

## 💬 Telegram krok za krokem

Telegram notifikace potřebují dvě věci: **token bota** a **chat_id** (kam posílat).

### A) Vytvoř si bota a získej token

1. V Telegramu najdi **@BotFather** (oficiální bot od Telegramu, má modrou fajfku).
2. Napiš mu `/newbot` a řiď se pokyny (vybereš jméno a uživatelské jméno bota).
3. BotFather ti pošle **token** — vypadá jako `123456789:AAE...`. Zkopíruj ho.
4. Vlož ho do `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAE...
   ```

### B) Získej svoje chat_id

1. V Telegramu **napiš svému nově vytvořenému botovi** libovolnou zprávu (třeba „ahoj").
   (Bez toho ti bot nemůže psát a chat_id se nedá zjistit.)
2. V terminálu spusť:
   ```bash
   python3 main.py --telegram-chatid
   ```
3. Vypíše se ti tvoje **chat_id** (číslo). Vlož ho do `.env`:
   ```
   TELEGRAM_CHAT_ID=987654321
   ```

### C) Otestuj propojení

```bash
python3 main.py --test-telegram
```

Mělo by ti do Telegramu dorazit „✅ Test: hlídač je propojený".

> **Tip:** Chceš, aby chodily notifikace víc lidem (třeba i kamarádovi)? Založ v Telegramu
> skupinu, přidej do ní bota a jako `TELEGRAM_CHAT_ID` použij ID skupiny (taky ho vypíše
> příkaz `--telegram-chatid`, když v té skupině něco napíšeš).

---

## 🧠 LLM zpracování (poplatky, energie, shrnutí)

Portály uvádějí poplatky za energie často jen v textu popisu („zálohy 3.500,- Kč/měs") — obyčejné
hledání to nespolehlivě vyčte. Proto popisy čte **LLM** a přesně z nich vytáhne měsíční poplatky
(kvůli správné **celkové ceně**), kauci, provizi a krátké shrnutí.

**Nastavení (doporučeno Gemini — má štědrý free tier):**

1. Vytvoř si klíč zdarma: **https://aistudio.google.com/apikey**
2. Vlož ho do `.env`:
   ```
   GEMINI_API_KEY=tvůj_klíč
   ```
3. Ověř:
   ```bash
   python3 main.py --test-llm
   ```

> Máš raději OpenAI nebo Groq? Stačí místo toho vyplnit `OPENAI_API_KEY` nebo `GROQ_API_KEY`.
> **Bez klíče to funguje taky** — jen se poplatky z textu vytahují jednodušším způsobem a víc bytů
> bude „poplatky neuvedeny". LLM se dá vypnout v `config.yaml` (`pouzit_llm: false`).
> Výsledky LLM se **cachují**, takže se stejný inzerát neposílá do modelu opakovaně (šetří kredit).

## ⏰ Automatické spouštění

Aby to hlídalo samo, nastav si pravidelné spouštění.

### Na Macu (cron)

1. Zjisti si absolutní cestu k projektu:
   ```bash
   pwd
   ```
2. Otevři editor cronu:
   ```bash
   crontab -e
   ```
3. Přidej řádek (uprav cestu). Tenhle spouští hlídač **každou hodinu mezi 8:00 a 22:00**:
   ```
   0 8-22 * * * /cesta/k/najemni-byty/run.sh --quiet >> /cesta/k/najemni-byty/hlidac.log 2>&1
   ```
4. Ulož a zavři. Skript `run.sh` se sám postará o virtuální prostředí.

> `run.sh` musí být spustitelný: `chmod +x run.sh` (stačí jednou).
> Pozn.: novější macOS může u cronu vyžadovat povolit Terminálu „Plný přístup k disku"
> (Nastavení → Soukromí a zabezpečení → Plný přístup k disku).

### Na Windows (Plánovač úloh)

1. Otevři **Plánovač úloh** (Task Scheduler).
2. **Vytvořit základní úlohu** → název „Hlídač bytů".
3. Spouštěč: **Denně** (nebo opakování po hodině v pokročilém nastavení).
4. Akce: **Spustit program** → Program/skript: nastav na `run.bat` v adresáři projektu
   (nebo „Spustit" = `run.bat`, „Začít v" = cesta k projektu).
5. Dokončit.

---

## ⚙️ Nastavení hledání (`config.yaml`)

```yaml
hledani:
  mesto: "Hradec Králové"      # hledá se JEN v tomto městě (vč. čtvrtí); okolní obce se vyřadí
  max_cena: 18000              # max. CELKOVÁ cena/měs = nájem + poplatky/energie (pokud jsou známy)
  min_plocha: 30               # menší se nevyřadí, jen dostanou nižší skóre
  min_dispozice: "1+kk"        # nejmenší akceptovaná dispozice
  vyzaduj_venkovni_prostor: false  # true = jen byty s balkonem/terasou/zahradou
  okoli: []                    # volitelně povol i okolní obce, např. ["Předměřice nad Labem"]

zdroje:                        # kterýkoli portál můžeš vypnout
  sreality: true
  bezrealitky: true
  ulovdomov: true
  idnes: true

provoz:
  max_stran_na_zdroj: 10
  detail_cache_dny: 14
  posilat_telegram: true
  pouzit_llm: true             # číst popisy přes LLM (poplatky/energie); bez API klíče se přeskočí
```

---

## 🖥️ Příkazy

| Příkaz | Co dělá |
|---|---|
| `python3 main.py` | jeden běh: stáhne, vyhodnotí, uloží dashboard, pošle Telegram |
| `python3 main.py --open` | totéž + otevře přehled v prohlížeči |
| `python3 main.py --source sreality` | spustí jen jeden portál (ladění) |
| `python3 main.py --test-telegram` | ověří propojení s Telegramem |
| `python3 main.py --telegram-chatid` | vypíše tvoje chat_id (pomůcka při nastavení) |
| `python3 main.py --test-llm` | ověří LLM klíč na ukázkovém inzerátu |
| `python3 main.py --quiet` | méně výpisů (vhodné pro cron) |

Výstupy najdeš ve složce `output/` (`index.html` = přehled, `listings.json` = data).

---

## 🔧 Časté problémy

- **„command not found: python3"** — nemáš nainstalovaný Python (viz krok 1).
- **Telegram nechodí** — zkontroluj, že jsi botovi napsal/a zprávu, že token i chat_id
  v `.env` sedí, a spusť `python3 main.py --test-telegram`.
- **Nějaký portál nic nevrací** — weby občas mění strukturu. Ostatní portály běží dál;
  spusť s `--source <portal>` a mrkni do výpisu. (Nahlas mi to, spravím.)
- **Chci začít načisto** — smaž soubor `state.db` (zapomene, co už vidělo).

---

## 🧩 Jak to funguje uvnitř (pro zvědavé)

```
main.py                 spouštěč (CLI)
hlidac/
  run.py                orchestrátor — spojí vše dohromady
  config.py             načtení config.yaml + .env
  http.py               sdílený HTTP klient (slušné zdržení, opakování při chybě)
  models.py             Listing — jednotný model bytu napříč portály
  scoring.py            filtrování + bodování (venkovní prostor váží nejvíc)
  store.py              SQLite — co už známe + cache detailů
  render.py             generování HTML dashboardu
  notify.py             Telegram
  scrapers/             jeden soubor na portál (sreality, bezrealitky, ulovdomov, idnes)
  templates/            HTML šablona dashboardu
```

Vše jede přes obyčejné HTTP dotazy na **veřejná data** portálů (Sreality a Ulovdomov mají
data přímo v HTML jako Next.js, Bezrealitky mají GraphQL API, iDNES je klasické HTML).
Žádné přihlašování, žádné proxy, žádné obcházení ochran.

### Playwright fallback (volitelné)

Kdyby některý portál v budoucnu začal blokovat obyčejné HTTP, dá se doplnit
[Playwright](https://playwright.dev/python/) (reálný prohlížeč). Pro současný provoz
**není potřeba** — proto není ani v základních závislostech.

---

## 🙏 Ohleduplnost

Nástroj je na **osobní použití**. Má vestavěné zdržení mezi dotazy, ať portály zbytečně
nezatěžuje. Nezvyšuj frekvenci na sekundy a data používej jen pro sebe. Skóre je jen
pomůcka pro řazení — finální rozhodnutí a ověření je vždy na tobě na webu daného portálu.

Hodně štěstí při hledání! 🍀
