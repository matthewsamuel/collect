"""
Collect&Go (Colruyt) scraper — https://www.collectandgo.be

The site is an Angular SPA that requires store selection before showing
products. We pick postal code 1000 (Brussels) as the default store.

If selectors break, run:  python probe.py colruyt
"""
from playwright.async_api import Page

from .base import BaseScraper

# ── Adjust these when the site updates ──────────────────────────────────────
SELECTORS = {
    # Store-selection modal
    "store_input": (
        "input[placeholder*='gemeente' i], input[placeholder*='postcode' i], "
        "input[placeholder*='zoek' i][type='search']"
    ),
    "store_option": "[role='option'], .store-suggestion, li[class*='suggestion' i]",
    "store_confirm": "button:has-text('Bevestig'), button:has-text('Selecteer'), button:has-text('Kies')",

    # Category links — now under /nl/assortiment/
    "category_links": "a[href*='/nl/assortiment/']",

    # Product grid — DS (design-system) tiles
    "product_card": (
        "a[class*='ds-product-tile'], "
        "[class*='cogo-product-tile' i], "
        "[data-test='product-tile'], [data-test='product-card'], "
        "[class*='product-tile' i], [class*='product-card' i]"
    ),

    # Fields within each card
    "name":       "[class*='title' i], [class*='name' i], h3, h2",
    "brand":      "[class*='brand' i], [class*='producer' i]",
    "price":      "[class*='price' i]:not([class*='unit' i]):not([class*='per' i])",
    "promo":      "[class*='promo' i], [class*='promotion' i], [class*='actie' i]",
    "unit_price": "[class*='unit-price' i], [class*='prijs-per' i], [class*='per-unit' i]",

    # Pagination
    "next_page": [
        "button[aria-label='Volgende pagina']",
        "button[aria-label='next' i]",
        ".pagination__next",
        "a:has-text('Volgende')",
        "button:has-text('Meer laden')",
        "button:has-text('Toon meer')",
    ],
}

# Paths that are not product category listings
_BLOCKLIST = {
    "/nl/assortiment/promo",
    "/nl/assortiment/barbecue-colruyt",
}


class ColruytScraper(BaseScraper):
    name = "Collect&Go"
    base_url = "https://www.collectandgo.be"

    async def scrape(self, page: Page, max_categories: int | None = None):
        # Root redirects to /nl/home; domcontentloaded avoids networkidle timeouts
        await page.goto(f"{self.base_url}/", wait_until="domcontentloaded", timeout=30_000)
        await self._dismiss_cookies(page)
        await self._select_store(page)
        await self._pause()

        categories = await self._get_categories(page)
        if max_categories:
            categories = categories[:max_categories]
        print(f"[{self.name}] {len(categories)} categories")

        for cat_name, cat_url in categories:
            await self._scrape_category(page, cat_name, cat_url)
            await self._pause()

    async def _select_store(self, page: Page):
        """Type a Brussels postal code and pick the first store suggestion."""
        try:
            inp = page.locator(SELECTORS["store_input"]).first
            if not await inp.is_visible(timeout=5000):
                return
            await inp.fill("1000")
            await page.wait_for_timeout(1200)
            option = page.locator(SELECTORS["store_option"]).first
            if await option.is_visible(timeout=3000):
                await option.click()
                await page.wait_for_timeout(1500)
            confirm = page.locator(SELECTORS["store_confirm"]).first
            if await confirm.is_visible(timeout=2000):
                await confirm.click()
                await page.wait_for_timeout(2000)
        except Exception as exc:
            print(f"[{self.name}] Store selection skipped: {exc}")

    async def _get_categories(self, page: Page) -> list[tuple[str, str]]:
        links = await page.locator(SELECTORS["category_links"]).all()
        seen: set[str] = set()
        result = []
        for link in links:
            href = (await link.get_attribute("href") or "").strip()
            text = (await link.inner_text()).strip().replace("\xa0", " ")
            if not href or not text:
                continue
            path = href.split("?")[0].rstrip("/")
            # Skip non-category paths; only keep top-level assortiment categories
            # (those that carry rootCategoryId are the main ones)
            if path in _BLOCKLIST:
                continue
            # Require rootCategoryId to filter out sub-categories
            if "rootCategoryId=" not in href:
                continue
            url = self._abs_url(href)
            if url not in seen:
                seen.add(url)
                result.append((text, url))
        return result

    async def _scrape_category(self, page: Page, category: str, url: str):
        print(f"[{self.name}] → {category}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)

        page_num = 1
        while True:
            await self._scroll_to_bottom(page)
            cards = await page.locator(SELECTORS["product_card"]).all()
            print(f"[{self.name}]   p{page_num}: {len(cards)} cards")
            for card in cards:
                await self._extract(card, category, page.url)

            if not await self._click_next_page(page, SELECTORS["next_page"]):
                break
            page_num += 1
            await self._pause()

    async def _extract(self, card, category: str, page_url: str):
        try:
            name = await self.safe_text(card, SELECTORS["name"])
            if not name:
                return
            href = await card.locator("a").first.get_attribute("href") or page_url
            self.add_product(
                name=name,
                brand=await self.safe_text(card, SELECTORS["brand"]),
                price=self.clean_price(await self.safe_text(card, SELECTORS["price"])),
                promo_price=self.clean_price(await self.safe_text(card, SELECTORS["promo"])),
                unit_price=await self.safe_text(card, SELECTORS["unit_price"]),
                category=category,
                url=self._abs_url(href),
            )
        except Exception:
            pass
