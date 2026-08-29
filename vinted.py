"""
Vinted-Datenquelle für den Discord-Sniper.

Nutzt die öffentliche Vinted-Katalog-Schnittstelle. Es werden keine
CAPTCHA-, Login- oder Anti-Bot-Umgehungen eingebaut.

Konfiguration über Railway-Variablen:
- VINTED_DOMAIN=de       (z.B. de, fr, nl, it)
- VINTED_PER_PAGE=96
- VINTED_TIMEOUT=4
"""

import os
import aiohttp


class VintedSource:
    def __init__(self):
        domain = os.getenv("VINTED_DOMAIN", "de").strip().lower()
        domain = domain.replace("https://", "").replace("http://", "").strip("/")
        self.base_url = f"https://www.vinted.{domain}"
        self.per_page = max(20, min(96, int(os.getenv("VINTED_PER_PAGE", "96"))))
        self.timeout = max(2, float(os.getenv("VINTED_TIMEOUT", "4")))

    async def search_new(self, query):
        params = {
            "search_text": query,
            "order": "newest_first",
            "page": 1,
            "per_page": self.per_page,
            "currency": "EUR",
        }

        url = f"{self.base_url}/api/v2/catalog/items"

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        }

        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, params=params) as response:
                    if response.status in (401, 403, 429):
                        raise RuntimeError(
                            f"Vinted antwortete mit HTTP {response.status}. "
                            "Die öffentliche Quelle ist momentan nicht direkt erreichbar."
                        )
                    if response.status != 200:
                        raise RuntimeError(f"Vinted HTTP {response.status}")

                    data = await response.json(content_type=None)

            raw_items = data.get("items", []) if isinstance(data, dict) else []
            return [self._normalize(item) for item in raw_items if item.get("id")]

        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Vinted-Verbindung fehlgeschlagen: {exc}") from exc

    @staticmethod
    def _normalize(item):
        price = item.get("price")
        if isinstance(price, dict):
            price = price.get("amount")

        photo = item.get("photo")
        image_url = photo.get("url") if isinstance(photo, dict) else None

        return {
            "id": str(item.get("id")),
            "title": item.get("title") or "Vinted-Angebot",
            "url": item.get("url") or "",
            "price": price,
            "size": item.get("size_title") or item.get("size") or "",
            "condition": item.get("status") or item.get("status_title") or "",
            "brand": item.get("brand_title") or item.get("brand") or "",
            "image_url": image_url,
        }

    def matches(self, item, sizes, max_price, condition):
        wanted_sizes = {
            x.strip().lower()
            for x in (sizes or "").split(",")
            if x.strip()
        }

        item_size = str(item.get("size", "")).strip().lower()
        if wanted_sizes and item_size not in wanted_sizes:
            return False

        if max_price is not None:
            try:
                if float(item.get("price")) > float(max_price):
                    return False
            except (TypeError, ValueError):
                return False

        if condition:
            wanted = condition.strip().lower()
            actual = str(item.get("condition", "")).lower()
            if wanted not in actual:
                return False

        return True
