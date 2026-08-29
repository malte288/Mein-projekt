"""
Vinted source using a normal anonymous public web session.
No Vinted login, CAPTCHA bypass, proxy rotation, or credential handling.

Gallery/timer additions:
- Basic catalog search stays fast.
- Full item details are fetched only for an item that already matched
  the profile filters, so the bot can get all photos + listing creation time.
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

    async def enrich_item(self, item):
        """
        Fetches the public item-detail endpoint for one already-matched item.
        This is used for the complete photo gallery and listing creation time.
        """
        item_id = item.get("id")
        if not item_id:
            return item

        session = await self._session()

        url = f"{self.base_url}/api/v2/items/{item_id}/details"

        try:
            async with session.get(url) as response:
                if response.status != 200:
                    return item

                data = await response.json(content_type=None)

            detail = data.get("item", data) if isinstance(data, dict) else {}
            if not isinstance(detail, dict):
                return item

            photos = []
            for photo in detail.get("photos", []) or []:
                if not isinstance(photo, dict):
                    continue
                photo_url = (
                    photo.get("full_size_url")
                    or photo.get("url")
                    or photo.get("high_resolution_url")
                )
                if photo_url:
                    photos.append(photo_url)

            # Keep order and remove duplicates.
            photos = list(dict.fromkeys(photos))

            item["image_urls"] = photos[:10]
            item["created_at"] = (
                detail.get("created_at_ts")
                or detail.get("created_at")
            )

            item["url"] = detail.get("url") or item.get("url") or ""

            # Prefer detail values if present.
            item["title"] = detail.get("title") or item.get("title")
            item["size"] = (
                detail.get("size_title")
                or detail.get("size")
                or item.get("size")
                or ""
            )
            item["condition"] = (
                detail.get("status_title")
                or detail.get("status")
                or item.get("condition")
                or ""
            )
            item["brand"] = (
                detail.get("brand_title")
                or item.get("brand")
                or ""
            )

            price = detail.get("price")
            if isinstance(price, dict):
                price = price.get("amount")
            if price is not None:
                item["price"] = price

        except Exception as e:
            print(f"Detail fuer Vinted-Artikel {item_id} nicht geladen: {e}")

        return item

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
            "image_urls": [],
            "created_at": item.get("created_at_ts") or item.get("created_at"),
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
