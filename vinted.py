"""
Vinted source using a normal anonymous public web session.
No Vinted login, CAPTCHA bypass, proxy rotation, or credential handling.
"""

import os
import aiohttp


class VintedSource:
    def __init__(self):
        domain = os.getenv("VINTED_DOMAIN", "de").strip().lower()
        domain = domain.replace("https://", "").replace("http://", "").strip("/")
        host = domain if domain.startswith("www.") else f"www.vinted.{domain}"

        self.base_url = f"https://{host}"
        self.per_page = max(20, min(96, int(os.getenv("VINTED_PER_PAGE", "96"))))
        self.timeout_seconds = max(3.0, float(os.getenv("VINTED_TIMEOUT", "5")))
        self.session = None

    async def _session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/json",
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                },
            )
        return self.session

    async def _bootstrap(self, session):
        async with session.get(
            f"{self.base_url}/catalog",
            params={"order": "newest_first"},
        ) as response:
            await response.read()
            return response.status

    async def search_new(self, query):
        session = await self._session()

        # Establish a normal anonymous web session first.
        bootstrap_status = await self._bootstrap(session)

        params = {
            "search_text": query,
            "order": "newest_first",
            "page": 1,
            "per_page": self.per_page,
            "currency": "EUR",
        }

        async with session.get(
            f"{self.base_url}/api/v2/catalog/items",
            params=params,
        ) as response:
            if response.status in (401, 403, 429):
                raise RuntimeError(
                    f"Vinted HTTP {response.status} after public session "
                    f"(catalog page returned {bootstrap_status})."
                )

            if response.status != 200:
                raise RuntimeError(f"Vinted HTTP {response.status}")

            data = await response.json(content_type=None)

        items = data.get("items", []) if isinstance(data, dict) else []
        return [
            self._normalize(item)
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]

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

        if wanted_sizes:
            item_size = str(item.get("size", "")).strip().lower()
            if item_size not in wanted_sizes:
                return False

        if max_price is not None:
            try:
                if float(item.get("price")) > float(max_price):
                    return False
            except (TypeError, ValueError):
                return False

        if condition:
            if condition.strip().lower() not in str(
                item.get("condition", "")
            ).lower():
                return False

        return True

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()
