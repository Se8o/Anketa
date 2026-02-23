import os

# Token pro reset hlasování – nastavit v .env nebo v prostředí Render.com
RESET_TOKEN = os.environ.get("RESET_TOKEN", "zmenit_pred_deploymentem")

# Cesta k souboru s hlasy (relativně k app.py)
DATA_FILE = os.environ.get("DATA_FILE", "data/votes.json")

# Možnosti ankety – klíč: ID, hodnota: popisek
CHOICES = {
    "a": "1–5 (jsem organizovaný člověk)",
    "b": "6–20 (standardní uživatel)",
    "c": "21–50 (power user)",
    "d": "51+ (záložky jsou způsob života)",
}

QUESTION = "Kolik otevřených záložek je ještě normální?"
