
"""
Vinted source using an anonymous public web session.

No Vinted login, CAPTCHA bypass, proxy rotation, or credential handling.
All HTTP text is kept in UTF-8; UI labels in the Discord bot are ASCII-safe
to avoid the mojibake seen in the previous deployment.
"""

import os
from datetime import datetime, timezone

import aiohttp


class VintedSource:
    def __init__(self):
        domain = os.getenv("VINTED_DOMAIN", "de").strip().lower()
        domain = domain.replace("https://", "").replace("http://", "").strip("/")
        host = domain if domain.startswith("www.") else f"www.vinted.{domain}"

        self.base_url = f"https://{host}"
        self.per_page = max(20, min(96, int(os.getenv("VINTED_PER_PAGE", "96"))))
        self.timeout_seconds = max(
            3.0,
            float(os.getenv("VINTED_TIMEOUT", "5")),
        )
        self.session = None

    async def _session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/json"
                    ),
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
        Get the full item after it already passed the profile filters.

        The details response commonly contains `photos` with all images.
        If the detail endpoint is unavailable, the catalog result is kept.
        """
        session = await self._session()
        item_id = str(item["id"])

        detail_urls = (
            f"{self.base_url}/api/v2/items/{item_id}/details",
            f"{self.base_url}/api/v2/items/{item_id}",
        )

        for url in detail_urls:
            try:
                async with session.get(
                    url,
                    params={"localize": "true"},
                ) as response:
                    if response.status != 200:
                        continue

                    data = await response.json(content_type=None)
                    detail = data.get("item", data) if isinstance(data, dict) else {}

                    if not isinstance(detail, dict):
                        continue

                    normalized = self._normalize(detail)

                    # Keep catalog values if the detail endpoint omits them.
                    for key, value in item.items():
                        if not normalized.get(key) and value:
                            normalized[key] = value

                    # Prefer all detail photos.
                    all_images = self._extract_images(detail)
                    if all_images:
                        normalized["image_urls"] = all_images

                    return normalized

            except (aiohttp.ClientError, TimeoutError, ValueError):
                continue

        return item

    @classmethod
    def _extract_images(cls, item):
        urls = []

        def add_url(value):
            if not value:
                return

            if isinstance(value, str):
                if value.startswith(("http://", "https://")):
                    urls.append(value)
                return

            if isinstance(value, dict):
                for key in (
                    "url",
                    "full_size_url",
                    "full_size",
                    "url_big",
                    "url_large",
                    "image_url",
                ):
                    candidate = value.get(key)
                    if isinstance(candidate, str) and candidate.startswith(
                        ("http://", "https://")
                    ):
                        urls.append(candidate)
                        return

        photos = item.get("photos")
        if isinstance(photos, list):
            for photo in photos:
                add_url(photo)

        # Some responses expose a single main photo as `photo`.
        add_url(item.get("photo"))

        # Other wrappers/responses use one of these names.
        for key in ("images", "image_urls", "photo_urls"):
            value = item.get(key)
            if isinstance(value, list):
                for entry in value:
                    add_url(entry)

        # Preserve order and remove duplicates.
        result = []
        seen = set()
        for url in urls:
            if url not in seen:
                seen.add(url)
                result.append(url)

        return result

    @classmethod
    def _parse_timestamp(cls, value):
        if value is None:
            return None

        if isinstance(value, (int, float)):
            # Vinted timestamps are normally seconds. Handle milliseconds too.
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000
            return int(ts)

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None

            try:
                numeric = float(text)
                if numeric > 10_000_000_000:
                    numeric /= 1000
                return int(numeric)
            except ValueError:
                pass

            try:
                normalized = text.replace("Z", "+00:00")
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except ValueError:
                return None

        return None

    @classmethod
    def _normalize(cls, item):
        price = item.get("price")
        if isinstance(price, dict):
            price = price.get("amount")

        image_urls = cls._extract_images(item)
        image_url = image_urls[0] if image_urls else None

        created_at = None
        for key in (
            "created_at_ts",
            "created_at",
            "upload_date",
            "created_at_timestamp",
        ):
            created_at = cls._parse_timestamp(item.get(key))
            if created_at:
                break

        return {
            "id": str(item.get("id")),
            "title": item.get("title") or "Vinted-Angebot",
            "url": item.get("url") or "",
            "price": price,
            "size": item.get("size_title") or item.get("size") or "",
            "condition": (
                item.get("status")
                or item.get("status_title")
                or ""
            ),
            "brand": item.get("brand_title") or item.get("brand") or "",
            "image_url": image_url,
            "image_urls": image_urls,
            "created_at_ts": created_at,
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
