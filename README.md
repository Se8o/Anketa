# Anketa – Záložkový průzkum

Jednoduchá hlasovací webová aplikace napsaná v Pythonu s Flaskem. Uživatel odpovídá na otázku, výsledky se ukládají na server a jsou sdílené mezi všemi návštěvníky. Hlasování lze resetovat pomocí tokenu.

**Stránku je možné navštívít na této adrese:** https://anketa-e1jd.onrender.com

## Jak to funguje

Aplikace má jeden HTML soubor (Jinja2 šablona), který se renderuje ve dvou stavech – formulář pro hlasování a stránka s výsledky. Flask na serveru obsluhuje čtyři URL adresy, ukládá hlasy do JSON souboru a odpovídá hotovým HTML.

```
Prohlížeč
    |
    |  GET /           --> zobrazí formulář s otázkou
    |  POST /vote      --> uloží hlas, přesměruje na výsledky
    |  GET /results    --> zobrazí aktuální výsledky
    |  POST /reset     --> smaže hlasy (jen se správným tokenem)
    |
Flask (app.py)
    |
    +-- VoteStore
           |
           +-- data/votes.json   (trvalé úložiště hlasů)
```

Přístup k souboru je chráněný zámkem (`threading.Lock`), takže při souběžných requestech nedojde k poškození dat. Zápis probíhá atomicky přes dočasný soubor, který se pak přejmenuje na cílový.

Reset hlasování vyžaduje token. Token se porovnává pomocí `hmac.compare_digest`, což brání útoku, kdy útočník tipuje token po znacích a měří dobu odpovědi.

## Technologie

**Backend** – Python 3.13, Flask 3, Gunicorn (WSGI server pro produkci).

**Frontend** – čisté HTML a CSS, žádný JavaScript. Animace progress barů jsou řešené CSS transitions. Výběr odpovědi je řešený CSS `:has()` selektorem bez JS.

**Úložiště** – JSON soubor na disku. Data přežijí restart aplikace, jsou sdílená pro všechny uživatele.

**Hosting** – Render.com, free tier. Každý push na GitHub spustí automatické nasazení.

**Konfigurace** – proměnné prostředí (`.env` lokálně, dashboard na Render.com). Token pro reset se nikdy nedostane do repozitáře.

## Struktura projektu

```
anketa/
    app.py                  hlavní aplikace, routes a logika
    config.py               konfigurace načtená z prostředí
    templates/
        index.html          šablona pro hlasování i výsledky
        error.html          stránka pro chyby 404 a 500
    data/
        votes.json          aktuální stav hlasování (auto-generováno)
    requirements.txt
    Procfile                start command pro Render.com
    .env.example            vzor konfiguračních proměnných
```

## Spuštění lokálně

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# nastav RESET_TOKEN v .env

flask run
```

Aplikace poběží na `http://localhost:5000`.

## Nasazení

Pro Render.com stačí v dashboardu nastavit:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Proměnná prostředí: `RESET_TOKEN`

Každý `git push` na větev `main` spustí automatické přenasazení.
