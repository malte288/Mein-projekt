"""
Vinted source.

- Anonymous public session
- Keeps the same aiohttp session + cookies
- Bootstraps the session from the public catalog
- Retries once with a fresh session after HTTP 401
- Does NOT bypass CAPTCHA / challenges
- Does NOT use proxies
- Does NOT use Vinted login credentials
- Keeps the existing bot interface:
    search_new()
    enrich_item()
    matches()
    close()

Gallery:
- image_urls
- image_url

Details:
- title
- price
- size
- condition
- brand
- description
- created_at
"""

import os
import asyncio
import aiohttp


class VintedSource:
    def __init__(self):
        domain = os.getenv(
            "VINTED_DOMAIN",
            "de",
        ).strip().lower()

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
                int(
                    os.getenv(
                        "VINTED_PER_PAGE",
                        "96",
                    )
                ),
            ),
        )

        self.timeout_seconds = max(
            3.0,
            float(
                os.getenv(
                    "VINTED_TIMEOUT",
                    "8",
                )
            ),
        )

        self.session = None

        # Prevent several simultaneous searches from all trying
        # to create a new Vinted session at the same time.
        self.session_lock = asyncio.Lock()

    # =========================================================
    # SESSION
    # =========================================================

    async def _create_session(self):
        """
        Creates a completely fresh anonymous HTTP session.

        aiohttp keeps Set-Cookie values automatically inside the
        ClientSession cookie jar.
        """

        if self.session is not None:
            try:
                await self.session.close()
            except Exception:
                pass

        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=self.timeout_seconds
            ),
            cookie_jar=aiohttp.CookieJar(),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/json;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": (
                    "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"
                ),
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )

        return self.session

    async def _session(self):
        """
        Returns the current session.

        If none exists, bootstrap a fresh one.
        """

        if (
            self.session is None
            or self.session.closed
        ):
            async with self.session_lock:
                if (
                    self.session is None
                    or self.session.closed
                ):
                    await self._bootstrap()

        return self.session

    # =========================================================
    # BOOTSTRAP
    # =========================================================

    async def _bootstrap(self):
        """
        Loads the public Vinted catalog page first.

        Vinted sets anonymous session cookies during this request.
        Those cookies remain inside the same aiohttp session and
        are then used for the JSON catalog request.
        """

        session = await self._create_session()

        url = f"{self.base_url}/catalog"

        params = {
            "order": "newest_first",
        }

        try:
            async with session.get(
                url,
                params=params,
                allow_redirects=True,
            ) as response:

                status = response.status

                # Consume the response so the connection can be
                # reused by aiohttp.
                await response.read()

                print(
                    f"[VINTED] Session bootstrap: HTTP {status}"
                )

                if status == 401:
                    raise RuntimeError(
                        "Vinted rejected the anonymous catalog "
                        "session with HTTP 401."
                    )

                if status == 403:
                    raise RuntimeError(
                        "Vinted rejected the anonymous catalog "
                        "session with HTTP 403."
                    )

                if status == 429:
                    raise RuntimeError(
                        "Vinted rate limited the catalog session "
                        "with HTTP 429."
                    )

                if status != 200:
                    raise RuntimeError(
                        f"Vinted catalog bootstrap HTTP {status}"
                    )

        except asyncio.TimeoutError:
            raise RuntimeError(
                "Vinted catalog bootstrap timed out."
            )

    # =========================================================
    # CATALOG SEARCH
    # =========================================================

    async def _catalog_request(self, query):
        """
        Performs one catalog API request using the current
        anonymous session.
        """

        session = await self._session()

        params = {
            "search_text": query,
            "order": "newest_first",
            "page": 1,
            "per_page": self.per_page,
            "currency": "EUR",
        }

        url = (
            f"{self.base_url}"
            "/api/v2/catalog/items"
        )

        async with session.get(
            url,
            params=params,
            allow_redirects=True,
        ) as response:

            status = response.status

            if status == 200:
                data = await response.json(
                    content_type=None
                )

                if not isinstance(data, dict):
                    raise RuntimeError(
                        "Vinted returned an unexpected "
                        "catalog response."
                    )

                return data

            if status == 401:
                raise RuntimeError(
                    "VINTED_401"
                )

            if status == 403:
                raise RuntimeError(
                    "VINTED_403"
                )

            if status == 429:
                raise RuntimeError(
                    "VINTED_429"
                )

            raise RuntimeError(
                f"Vinted HTTP {status}"
            )

    async def search_new(self, query):
        """
        Searches newest Vinted listings.

        On HTTP 401:
            refresh anonymous session once
            retry once

        On HTTP 403 / 429:
            stop immediately and report the problem.

        This intentionally does not attempt to bypass a challenge.
        """

        query = (query or "").strip()

        if not query:
            return []

        # First attempt.
        try:
            data = await self._catalog_request(
                query
            )

        except RuntimeError as error:

            message = str(error)

            # -------------------------------------------------
            # SESSION EXPIRED
            # -------------------------------------------------

            if message == "VINTED_401":

                print(
                    "[VINTED] HTTP 401 - "
                    "refreshing anonymous session."
                )

                async with self.session_lock:

                    # Another coroutine may already have refreshed
                    # the session while we waited for the lock.
                    #
                    # For safety, create a fresh session here.
                    await self._bootstrap()

                try:
                    data = await self._catalog_request(
                        query
                    )

                except RuntimeError as retry_error:

                    print(
                        "[VINTED] Retry after session refresh "
                        f"failed: {retry_error}"
                    )

                    raise

            else:
                raise

        items = (
            data.get("items", [])
            if isinstance(data, dict)
            else []
        )

        results = []

        for raw_item in items:

            if not isinstance(raw_item, dict):
                continue

            if not raw_item.get("id"):
                continue

            results.append(
                self._normalize(raw_item)
            )

        return results

    # =========================================================
    # ITEM DETAILS
    # =========================================================

    async def enrich_item(self, item):
        """
        Fetches the public item detail endpoint for a matched item.

        This is intentionally called only after the normal
        catalog filters have matched the item.
        """

        item_id = item.get("id")

        if not item_id:
            return item

        session = await self._session()

        # Keep compatibility with the previous implementation.
        urls = [
            f"{self.base_url}"
            f"/api/v2/items/{item_id}/details",

            f"{self.base_url}"
            f"/api/v2/items/{item_id}",
        ]

        for url in urls:

            try:
                async with session.get(
                    url,
                    allow_redirects=True,
                ) as response:

                    if response.status == 401:
                        print(
                            f"[DETAIL] HTTP 401 for {item_id}"
                        )
                        return item

                    if response.status != 200:
                        continue

                    data = await response.json(
                        content_type=None
                    )

                if not isinstance(data, dict):
                    return item

                detail = data.get(
                    "item",
                    data,
                )

                if not isinstance(detail, dict):
                    return item

                self._merge_details(
                    item,
                    detail,
                )

                return item

            except asyncio.TimeoutError:

                print(
                    f"[DETAIL] Timeout for "
                    f"Vinted item {item_id}"
                )

                return item

            except Exception as error:

                print(
                    f"[DETAIL] Item {item_id} "
                    f"could not be loaded: {error}"
                )

                return item

        return item

    # =========================================================
    # DETAIL MERGE
    # =========================================================

    @staticmethod
    def _merge_details(
        item,
        detail,
    ):
        # -----------------------------------------------------
        # PHOTOS
        # -----------------------------------------------------

        photos = []

        raw_photos = (
            detail.get("photos")
            or detail.get("images")
            or []
        )

        if isinstance(raw_photos, list):

            for photo in raw_photos:

                if isinstance(photo, str):

                    value = photo

                elif isinstance(photo, dict):

                    value = (
                        photo.get("full_size_url")
                        or photo.get("high_resolution_url")
                        or photo.get("url")
                    )

                else:

                    value = None

                if value:
                    value = str(value)

                    if value not in photos:
                        photos.append(value)

        # Keep the existing main image as a fallback.
        existing = item.get("image_url")

        if existing and existing not in photos:
            photos.insert(
                0,
                existing,
            )

        item["image_urls"] = photos[:10]

        if item["image_urls"]:
            item["image_url"] = (
                item["image_urls"][0]
            )

        # -----------------------------------------------------
        # TITLE
        # -----------------------------------------------------

        item["title"] = (
            detail.get("title")
            or item.get("title")
            or "Vinted-Angebot"
        )

        # -----------------------------------------------------
        # DESCRIPTION
        # -----------------------------------------------------

        item["description"] = (
            detail.get("description")
            or item.get("description")
            or ""
        )

        # -----------------------------------------------------
        # URL
        # -----------------------------------------------------

        item["url"] = (
            detail.get("url")
            or item.get("url")
            or ""
        )

        # -----------------------------------------------------
        # SIZE
        # -----------------------------------------------------

        item["size"] = (
            detail.get("size_title")
            or detail.get("size")
            or item.get("size")
            or ""
        )

        # -----------------------------------------------------
        # CONDITION
        # -----------------------------------------------------

        item["condition"] = (
            detail.get("status_title")
            or detail.get("status")
            or item.get("condition")
            or ""
        )

        # -----------------------------------------------------
        # BRAND
        # -----------------------------------------------------

        item["brand"] = (
            detail.get("brand_title")
            or detail.get("brand")
            or item.get("brand")
            or ""
        )

        # -----------------------------------------------------
        # PRICE
        # -----------------------------------------------------

        price = detail.get("price")

        if isinstance(price, dict):
            price = price.get("amount")

        if price is not None:
            item["price"] = price

        # -----------------------------------------------------
        # CREATION TIME
        # -----------------------------------------------------

        item["created_at"] = (
            detail.get("created_at_ts")
            or detail.get("created_at")
            or item.get("created_at")
        )

    # =========================================================
    # NORMALIZE CATALOG ITEM
    # =========================================================

    @staticmethod
    def _normalize(item):

        # -----------------------------------------------------
        # PRICE
        # -----------------------------------------------------

        price = item.get("price")

        if isinstance(price, dict):
            price = price.get("amount")

        # -----------------------------------------------------
        # MAIN IMAGE
        # -----------------------------------------------------

        image_url = None

        photo = item.get("photo")

        if isinstance(photo, dict):

            image_url = (
                photo.get("url")
                or photo.get("full_size_url")
                or photo.get("high_resolution_url")
            )

        elif isinstance(photo, str):

            image_url = photo

        # -----------------------------------------------------
        # IMAGE LIST
        # -----------------------------------------------------

        image_urls = []

        raw_photos = (
            item.get("photos")
            or item.get("images")
            or []
        )

        if isinstance(raw_photos, list):

            for photo in raw_photos:

                if isinstance(photo, str):

                    value = photo

                elif isinstance(photo, dict):

                    value = (
                        photo.get("url")
                        or photo.get("full_size_url")
                        or photo.get("high_resolution_url")
                    )

                else:

                    value = None

                if value:
                    value = str(value)

                    if value not in image_urls:
                        image_urls.append(value)

        if (
            image_url
            and image_url not in image_urls
        ):
            image_urls.insert(
                0,
                image_url,
            )

        return {
            "id": str(
                item.get("id")
            ),

            "title": (
                item.get("title")
                or "Vinted-Angebot"
            ),

            "url": (
                item.get("url")
                or ""
            ),

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

    # =========================================================
    # PROFILE FILTERS
    # =========================================================

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
            value.strip().lower()
            for value in (
                sizes or ""
            ).split(",")
            if value.strip()
        }

        if wanted_sizes:

            item_size = (
                str(
                    item.get(
                        "size",
                        "",
                    )
                )
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
                condition
                .strip()
                .lower()
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

    # =========================================================
    # CLOSE
    # =========================================================

    async def close(self):

        if (
            self.session is not None
            and not self.session.closed
        ):
            await self.session.close()

        self.session = None
