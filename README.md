# Vinted Sniper V2

Schneller Discord-Alert-Bot mit:
- Start/Pause-Buttons
- Suchprofilen
- Preis-/Größen-/Zustandsfiltern
- Duplikat-Schutz
- Discord-Embeds mit Bild und Link
- Erkennungszeit-Messung
- SQLite-Persistenz

## Wichtig
Die Datei `vinted.py` enthält absichtlich noch keinen Vinted-Abruf.
Für echte Treffer muss dort eine zulässige, stabile Datenquelle/API
angebunden werden. Kein CAPTCHA-/Anti-Bot-Bypass ist enthalten.

## Einrichtung
1. `.env.example` zu `.env` kopieren.
2. Deinen Discord-Bot-Token in `.env` eintragen.
3. `pip install -r requirements.txt`
4. `python bot.py`

Beispiel:
`/setup name:Ralph query:"Ralph Lauren Polo" sizes:M,L max_price:20 condition:"Sehr gut"`
