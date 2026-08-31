"""
Vinted source using the public Vinted catalog pages.

- No Vinted login
- No CAPTCHA bypass
- No proxy rotation
- No credential handling
- Catalog search reads the public HTML page
- Item details are fetched only after an item matches
- Supports multiple photos
- Tries to extract description + creation time
"""

import os
import json
import re
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin

import aiohttp


class VintedSource:
    def __init__(self):
        domain = os.getenv("VINTED_DOMAIN", "de").strip().lower()

        domain = (
            domain
            .replace("https://", "")
            .replace("http://", "")
            .strip("/")
        )

        host = (
            domain
            if domain.startswith("www.")
            else f"www.vinted.{domain}"
        )

        self.base_url = f"https://{host}"

        self.per_page = max(
            20,
            min(
                96,
                int(os.getenv("VINTED_PER_PAGE", "96"))
            ),
        )

        self.timeout_seconds = max(
            3.0,
            float(os.getenv("VINTED_TIMEOUT", "8"))
        )

        self.session = None

    # ---------------------------------------------------------
    # HTTP SESSION
    # ---------------------------------------------------------

    async def _session(self):
        if self.session is None or self.session.closed:

            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=self.timeout_seconds
                ),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,image/avif,"
                        "image/webp,*/*;q=0.8"
                    ),
                    "Accept-Language": (
                        "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"
                    ),
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )

        return self.session

    # ---------------------------------------------------------
    # PUBLIC CATALOG URL
    # ---------------------------------------------------------

    def _catalog_url(self, query, page=1):
        params = {
            "search_text": query,
            "order": "newest_first",
            "page": page,
            "per_page": self.per_page,
        }

        return (
            f"{self.base_url}/catalog?"
            f"{urlencode(params)}"
        )

    # ---------------------------------------------------------
    # JSON ARRAY EXTRACTION
    # ---------------------------------------------------------

    @staticmethod
    def _extract_json_array_after_key(text, key):
        """
        Finds:

            "items":[...]

        inside the Vinted HTML and lets Python's JSON decoder
        determine where the array actually ends.

        This is considerably safer than using a greedy regex.
        """

        patterns = [
            f'"{key}":',
            f'"{key}" :',
        ]

        start = -1

        for pattern in patterns:
            start = text.find(pattern)

            if start != -1:
                start += len(pattern)
                break

        if start == -1:
            return None

        while start < len(text) and text[start].isspace():
            start += 1

        if start >= len(text):
            return None

        if text[start] != "[":
            return None

        try:
            decoder = json.JSONDecoder()

            value, _ = decoder.raw_decode(
                text[start:]
            )

            if isinstance(value, list):
                return value

        except Exception:
            return None

        return None

    # ---------------------------------------------------------
    # CATALOG HTML
    # ---------------------------------------------------------

    async def _fetch_catalog_html(self, query):
        session = await self._session()

        url = self._catalog_url(
            query=query,
            page=1,
        )

        async with session.get(
            url,
            allow_redirects=True,
        ) as response:

            status = response.status
            text = await response.text(
                errors="replace"
            )

            if status == 401:
                raise RuntimeError(
                    "Vinted HTTP 401 auf der öffentlichen "
                    "Katalogseite."
                )

            if status == 403:
                raise RuntimeError(
                    "Vinted HTTP 403 auf der öffentlichen "
                    "Katalogseite."
                )

            if status == 429:
                raise RuntimeError(
                    "Vinted HTTP 429 - zu viele Anfragen."
                )

            if status != 200:
                raise RuntimeError(
                    f"Vinted HTTP {status}"
                )

            return text

    # ---------------------------------------------------------
    # SEARCH
    # ---------------------------------------------------------

    async def search_new(self, query):
        """
        Searches the public Vinted catalog.

        IMPORTANT:
        This no longer calls:

            /api/v2/catalog/items

        because that endpoint currently returns HTTP 401
        for the anonymous Railway session.
        """

        query = (query or "").strip()

        if not query:
            return []

        html = await self._fetch_catalog_html(
            query
        )

        items = self._extract_json_array_after_key(
            html,
            "items",
        )

        if items is None:

            # Helpful diagnostics in Railway logs.
            lowered = html.lower()

            if (
                "captcha" in lowered
                or "challenge" in lowered
                or "datadome" in lowered
                or "cf-mitigated" in lowered
            ):
                raise RuntimeError(
                    "Vinted hat eine Challenge/Anti-Bot-Seite "
                    "statt des Katalogs geliefert."
                )

            raise RuntimeError(
                "Vinted-Katalog konnte geladen werden, "
                "aber die eingebetteten Artikel konnten "
                "nicht gefunden werden."
            )

        normalized = []

        for item in items:

            if not isinstance(item, dict):
                continue

            if not item.get("id"):
                continue

            normalized.append(
                self._normalize(item)
            )

        return normalized

    # ---------------------------------------------------------
    # ITEM DETAIL PAGE
    # ---------------------------------------------------------

    async def enrich_item(self, item):
        """
        Loads the public Vinted item page only after the item
        already matched the profile.

        Attempts to collect:

        - all available image URLs
        - description
        - creation/publication time
        - title
        - size
        - condition
        - brand
        - price
        """

        item_id = item.get("id")

        if not item_id:
            return item

        session = await self._session()

        item_url = item.get("url")

        if not item_url:
            item_url = (
                f"{self.base_url}/items/{item_id}"
            )

        try:
            async with session.get(
                item_url,
                allow_redirects=True,
            ) as response:

                if response.status != 200:
                    print(
                        f"[DETAIL] Vinted HTTP "
                        f"{response.status} für {item_id}"
                    )

                    return item

                html = await response.text(
                    errors="replace"
                )

            # -------------------------------------------------
            # PHOTOS
            # -------------------------------------------------

            photos = []

            # Vinted image URLs are usually visible in the
            # public item HTML.
            image_matches = re.findall(
                r'https://images\d+\.vinted\.net/[^"\'<>\s\\]+',
                html,
            )

            for image_url in image_matches:

                image_url = (
                    image_url
                    .replace("\\/", "/")
                    .replace("\\u0026", "&")
                )

                if image_url not in photos:
                    photos.append(image_url)

            # Also try common JSON image fields.
            extra_patterns = [
                r'"full_size_url":"([^"]+)"',
                r'"high_resolution_url":"([^"]+)"',
                r'"image_url":"([^"]+)"',
                r'"url":"(https://images\d+\.vinted\.net/[^"]+)"',
            ]

            for pattern in extra_patterns:

                matches = re.findall(
                    pattern,
                    html,
                )

                for image_url in matches:

                    image_url = (
                        image_url
                        .replace("\\/", "/")
                        .replace("\\u0026", "&")
                    )

                    if (
                        image_url
                        and image_url not in photos
                    ):
                        photos.append(image_url)

            # Remove duplicates while preserving order.
            photos = list(dict.fromkeys(photos))

            item["image_urls"] = photos[:10]

            if photos:
                item["image_url"] = photos[0]

            # -------------------------------------------------
            # JSON-LD
            # -------------------------------------------------

            json_ld_blocks = re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
                r'(.*?)'
                r'</script>',
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )

            json_ld_objects = []

            for block in json_ld_blocks:

                block = block.strip()

                if not block:
                    continue

                try:
                    parsed = json.loads(block)

                    if isinstance(parsed, list):
                        json_ld_objects.extend(
                            parsed
                        )
                    else:
                        json_ld_objects.append(
                            parsed
                        )

                except Exception:
                    continue

            for data in json_ld_objects:

                if not isinstance(data, dict):
                    continue

                # -------------------------------------------------
                # TITLE
                # -------------------------------------------------

                if data.get("name"):
                    item["title"] = data["name"]

                # -------------------------------------------------
                # DESCRIPTION
                # -------------------------------------------------

                if data.get("description"):
                    item["description"] = (
                        str(data["description"]).strip()
                    )

                # -------------------------------------------------
                # CREATED / PUBLISHED
                # -------------------------------------------------

                if data.get("datePublished"):
                    item["created_at"] = (
                        data["datePublished"]
                    )

                # -------------------------------------------------
                # URL
                # -------------------------------------------------

                if data.get("url"):
                    item["url"] = data["url"]

                # -------------------------------------------------
                # IMAGE
                # -------------------------------------------------

                images = data.get("image")

                if isinstance(images, str):

                    if images not in photos:
                        photos.append(images)

                elif isinstance(images, list):

                    for image in images:

                        if (
                            isinstance(image, str)
                            and image not in photos
                        ):
                            photos.append(image)

            item["image_urls"] = list(
                dict.fromkeys(photos)
            )[:10]

            if item["image_urls"]:
                item["image_url"] = (
                    item["image_urls"][0]
                )

            # -------------------------------------------------
            # META FALLBACKS
            # -------------------------------------------------

            title = self._meta_content(
                html,
                "og:title",
            )

            if title:
                item["title"] = title

            description = self._meta_content(
                html,
                "description",
            )

            if description:
                item["description"] = (
                    item.get("description")
                    or description
                )

            canonical = self._meta_content(
                html,
                "og:url",
            )

            if canonical:
                item["url"] = canonical

        except asyncio.TimeoutError:

            print(
                f"[DETAIL] Timeout für Vinted-Artikel "
                f"{item_id}"
            )

        except Exception as e:

            print(
                f"[DETAIL] Artikel {item_id} "
                f"nicht geladen: {e}"
            )

        return item

    # ---------------------------------------------------------
    # META CONTENT
    # ---------------------------------------------------------

    @staticmethod
    def _meta_content(html, name):
        patterns = [
            rf'<meta[^>]+property=["\']{re.escape(name)}'
            rf'["\'][^>]+content=["\']([^"\']*)["\']',

            rf'<meta[^>]+name=["\']{re.escape(name)}'
            rf'["\'][^>]+content=["\']([^"\']*)["\']',

            rf'<meta[^>]+content=["\']([^"\']*)["\']'
            rf'[^>]+property=["\']{re.escape(name)}'
            rf'["\']',
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                html,
                flags=re.IGNORECASE,
            )

            if match:
                return (
                    match.group(1)
                    .replace("&amp;", "&")
                    .replace("&quot;", '"')
                    .strip()
                )

        return None

    # ---------------------------------------------------------
    # NORMALIZE CATALOG ITEM
    # ---------------------------------------------------------

    @staticmethod
    def _normalize(item):

        price = item.get("price")

        if isinstance(price, dict):
            price = price.get("amount")

        # -----------------------------------------------------
        # MAIN PHOTO
        # -----------------------------------------------------

        photo = item.get("photo")

        image_url = None

        if isinstance(photo, dict):

            image_url = (
                photo.get("url")
                or photo.get("full_size_url")
                or photo.get("high_resolution_url")
            )

        elif isinstance(photo, str):

            image_url = photo

        # -----------------------------------------------------
        # PHOTO LIST
        # -----------------------------------------------------

        image_urls = []

        raw_photos = (
            item.get("photos")
            or item.get("images")
            or []
        )

        if isinstance(raw_photos, list):

            for entry in raw_photos:

                if isinstance(entry, str):

                    value = entry

                elif isinstance(entry, dict):

                    value = (
                        entry.get("url")
                        or entry.get("full_size_url")
                        or entry.get("high_resolution_url")
                    )

                else:

                    value = None

                if value and value not in image_urls:

                    image_urls.append(
                        str(value)
                    )

        if (
            image_url
            and image_url not in image_urls
        ):
            image_urls.insert(
                0,
                image_url,
            )

        # -----------------------------------------------------
        # URL
        # -----------------------------------------------------

        url = item.get("url") or ""

        if url.startswith("/"):
            url = urljoin(
                "https://www.vinted.de",
                url,
            )

        # -----------------------------------------------------
        # NORMALIZED ITEM
        # -----------------------------------------------------

        return {
            "id": str(item.get("id")),

            "title": (
                item.get("title")
                or "Vinted-Angebot"
            ),

            "url": url,

            "price": price,

            "size": (
                item.get("size_title")
                or item.get("size")
                or ""
            ),

            "condition": (
                item.get("status_title")
                or item.get("status")
                or ""
            ),

            "brand": (
                item.get("brand_title")
                or item.get("brand")
                or ""
            ),

            "description": (
                item.get("description")
                or ""
            ),

            "image_url": image_url,

            "image_urls": image_urls[:10],

            "created_at": (
                item.get("created_at_ts")
                or item.get("created_at")
            ),
        }

    # ---------------------------------------------------------
    # FILTER
    # ---------------------------------------------------------

    def matches(
        self,
        item,
        sizes,
        max_price,
        condition,
    ):

        # -----------------------------------------------------
        # SIZE
        # -----------------------------------------------------

        wanted_sizes = {
            x.strip().lower()
            for x in (sizes or "").split(",")
            if x.strip()
        }

        if wanted_sizes:

            item_size = (
                str(item.get("size", ""))
                .strip()
                .lower()
            )

            if item_size not in wanted_sizes:
                return False

        # -----------------------------------------------------
        # PRICE
        # -----------------------------------------------------

        if max_price is not None:

            try:

                item_price = float(
                    item.get("price")
                )

                if item_price > float(
                    max_price
                ):
                    return False

            except (
                TypeError,
                ValueError,
            ):

                return False

        # -----------------------------------------------------
        # CONDITION
        # -----------------------------------------------------

        if condition:

            wanted_condition = (
                condition.strip().lower()
            )

            actual_condition = (
                str(
                    item.get(
                        "condition",
                        "",
                    )
                )
                .strip()
                .lower()
            )

            if (
                wanted_condition
                not in actual_condition
            ):
                return False

        return True

    # ---------------------------------------------------------
    # CLOSE
    # ---------------------------------------------------------

    async def close(self):

        if (
            self.session is not None
            and not self.session.closed
        ):

            await self.session.close()
