# 🏡 Hlídač bytů

Malý pomocník, který za tebe každý den projde inzeráty s byty k pronájmu,
vybere jen ty, co dávají smysl (v Hradci Králové, do tvého rozpočtu, s balkonem
nebo zahradou), spočítá **skutečnou cenu i s poplatky** a přehledně ti je ukáže.

Nemusíš nic umět. Stačí popořadě udělat kroky níže. Zabere to asi **15 minut** a
většinu z toho jen čekáš, až se něco stáhne. 💛

---

## 📖 Než začneš — 3 věci, které je dobré vědět

1. **„Terminál"** je taková aplikace na Macu — okno, kam se píšou příkazy.
   Vypadá nudně (černé/bílé okno s textem), ale neboj, ty budeš jen **kopírovat
   a vkládat** hotové příkazy odsud. Nemusíš nic vymýšlet.

2. U každého kroku je **šedý rámeček** s příkazem. Najeď na něj myší a vpravo
   nahoře se objeví **ikonka na kopírování** 📋 — klikni na ni. Pak přepni do
   Terminálu, zmáčkni **Cmd + V** (vložit) a **Enter**. Hotovo.

3. Po každém příkazu chvilku **počkej**, než se text v okně zastaví. Někdy se
   toho vypíše hodně — to je v pořádku, tak to má být.

> Kdyby se kdykoli objevila **červená chyba** nebo se něco zaseklo, nic nerozbiješ.
> Vyfoť to a pošli mi to — spravím to. 🙂

---

## 🛠️ Nastavení — uděláš jen jednou

### Krok 1 — Otevři Terminál

Zmáčkni **Cmd + mezerník** (otevře se vyhledávání), napiš **Terminál** a zmáčkni
**Enter**. Otevře se to okno, o kterém byla řeč.

### Krok 2 — Stáhni Hlídače

Zkopíruj a vlož tento příkaz (pak Enter):

```
git clone https://github.com/jakubcurik/hlidac-bytu.git ~/hlidac-bytu
```

> **Může vyskočit okno** „Chcete nainstalovat nástroje pro příkazový řádek?" —
> klikni na **Instalovat**, odsouhlas a počkej (pár minut). Až to doběhne,
> vlož příkaz výše ještě jednou. Tímhle se rovnou nainstaluje všechno potřebné.

### Krok 3 — Nainstaluj, co Hlídač potřebuje

Zkopíruj a vlož celý tento řádek najednou (pak Enter) a chvíli počkej:

```
cd ~/hlidac-bytu && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Vypíše se spousta řádků, jak se stahují součástky. Až se to zastaví a napíše
něco jako „Successfully installed…", je hotovo. ✅

### Krok 4 — Přidej klíč, aby uměl počítat ceny přesně

Hlídač si umí přečíst inzerát a spočítat cenu i s energiemi. K tomu potřebuje
**bezplatný klíč od Googlu**. Získáš ho takhle:

1. Otevři stránku **https://aistudio.google.com/apikey**
2. Přihlas se svým Google účtem (Gmail).
3. Klikni na **Create API key** (Vytvořit klíč) a klíč si **zkopíruj** —
   je to dlouhý řádek písmen a čísel.

Teď ho vložíme do Hlídače. V Terminálu vlož (pak Enter):

```
cp .env.example .env && open -e .env
```

Otevře se textový editor se souborem. Najdi řádek:

```
GEMINI_API_KEY=
```

a hned **za rovnítko vlož svůj zkopírovaný klíč** (bez mezery), takže to bude
vypadat třeba `GEMINI_API_KEY=AIzaSy...`. Pak **ulož** (Cmd + S) a okno **zavři**.

> Tenhle krok můžeš i přeskočit — Hlídač bude fungovat i bez klíče, jen bude
> počítat poplatky méně přesně. Ale s klíčem (je zdarma) to umí mnohem líp.

### Krok 5 — Zapni si nastavení hledání

Vlož (pak Enter):

```
cp config.example.yaml config.yaml
```

A je hotovo! Ve výchozím stavu hledá **byty v Hradci Králové do 18 000 Kč měsíčně
(i s poplatky), s balkonem, terasou nebo zahradou**. Když bys někdy chtěla něco
změnit (třeba cenu), napiš mi, ukážu ti kde.

---

## ▶️ Jak Hlídače spustit

Kdykoli se budeš chtít podívat na aktuální byty, otevři Terminál (Krok 1) a vlož
tenhle jeden příkaz (pak Enter):

```
cd ~/hlidac-bytu && .venv/bin/python main.py --open
```

Chvilku to poběží (prochází inzeráty a počítá ceny — **napoprvé to trvá asi
minutu**) a pak se ti **samo otevře v prohlížeči** přehled bytů. 🎉

Nahoře v přehledu si můžeš byty **filtrovat** (podle ceny, velikosti, dispozice…)
a řadit. Každý byt má tlačítko **„Zobrazit inzerát"**, které tě přenese na
původní stránku.

> Tip: příkaz je pořád stejný. Můžeš si ho uložit někam do poznámek a příště
> jen zkopírovat.

---

## 🔔 Chceš upozornění na mobil? (nepovinné)

Hlídač umí posílat zprávy na **Telegram** pokaždé, když se objeví nový vyhovující
byt — ať ti žádný neuteče. Nastavení je trošku delší, tak jestli o to stojíš,
**řekni mi** a rozjedu ti to za pár minut. Není to nutné — přehled v
prohlížeči funguje i bez toho.

---

## 🤖 Aby to hlídalo samo každý den (nepovinné)

Když budeš chtít, aby Hlídač běžel automaticky sám (třeba každé ráno) a ty ses
o nic nestarala, jde to nastavit. Je to ale technič­tější, tak na to taky
**stačí říct mně**. Do té doby ho klidně spouštěj ručně příkazem výše
kdykoli tě napadne mrknout na nabídku.

---

## ❓ Když něco nefunguje

- **„command not found" nebo červená chyba** → vyfoť okno a pošli mi to.
  Nejspíš jen chybí nějaká drobnost v nastavení.
- **Neotevřel se přehled** → v Terminálu zkontroluj, jestli tam není chyba.
  Přehled je i tak uložený — najdeš ho ve složce `hlidac-bytu` → `output` →
  soubor `index.html` (dvojklik ho otevře v prohlížeči).
- **Nenašel skoro žádné byty** → to je normální, když je zrovna málo nabídek,
  které splňují všechno (cena, balkon/zahrada, Hradec). Zkus to za pár dní.

---

Držím palce při hledání! 🍀 A neboj se ptát — od toho tu jsem. 💛

<sub>Máš počítač s Windows, ne Mac? Napiš mi, pošlu ti postup pro Windows.</sub>
