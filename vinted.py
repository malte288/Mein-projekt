"""
Vinted-Datenquellen-Adapter.

Hier muss eine zulässige/stabile Datenquelle für öffentliche Angebote
angebunden werden. Dieser Starter enthält absichtlich keinen Anti-Bot-,
CAPTCHA- oder Login-Bypass.

Die restliche Bot-Logik (Discord, Filter, Datenbank, Start/Pause,
Duplikate und Timing) ist unabhängig davon.
"""

class VintedSource:
    async def search_new(self, query):
        # Platzhalter: liefert aktuell keine Angebote.
        return []

    def matches(self, item, sizes, max_price, condition):
        wanted_sizes = {x.strip().lower() for x in (sizes or "").split(",") if x.strip()}
        if wanted_sizes and str(item.get("size","")).lower() not in wanted_sizes:
            return False
        if max_price is not None:
            try:
                if float(item.get("price")) > float(max_price):
                    return False
            except (TypeError, ValueError):
                return False
        if condition and condition.lower() not in str(item.get("condition","")).lower():
            return False
        return True
