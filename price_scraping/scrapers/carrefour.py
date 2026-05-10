"""
Carrefour Belgium scraper — https://www.carrefour.be/nl

If selectors break, run:  python probe.py carrefour
"""
from playwright.async_api import Page

from .base import BaseScraper

SELECTORS = {
    "category_links": (
        "nav a[href*='/nl/'], [class*='CategoryList' i] a, "
        "[class*='category' i] a[href*='/nl/']"
    ),
    "product_card": (
        "[data-test='product-card'], [data-testid='product-card'], "
        "[class*='ProductCard' i], [class*='product-card' i], "
        "[class*='product-tile' i], li[class*='product' i], "
        "article[class*='product' i]"
    ),
    "name":       "[class*='title' i], [class*='name' i], h3, h2",
    "brand":      "[class*='brand' i]",
    "price":      "[class*='Price' i]:not([class*='unit' i]):not([class*='per' i])",
    "promo":      "[class*='promo' i], [class*='discount' i], [class*='actie' i]",
    "unit_price": "[class*='unit' i][class*='price' i], [class*='per-unit' i], [class*='prijs-per' i]",
    "next_page": [
        "[aria-label='Next']",
        "[aria-label='Volgende']",
        "button:has-text('Volgende')",
        "button:has-text('Toon meer')",
        "button:has-text('Meer producten')",
        ".pagination__next",
        "a[rel='next']",
    ],
}

# Carrefour's grocery section lives under /nl/voeding/ or /nl/courses-en-ligne/
_GROCERY_PATH = "/nl/"


class CarrefourScraper(BaseScraper):
    name = "Carrefour"
    base_url = "https://www.carrefour.be"

    async def scrape(self, page: Page, max_categories: int | None = None):
        await page.goto(f"{self.base_url}/nl", wait_until="domcontentloaded", timeout=30_000)
        await self._dismiss_cookies(page)
        await self._pause()

        categories = await self._get_categories(page)
        if max_categories:
            categories = categories[:max_categories]
        print(f"[{self.name}] {len(categories)} categories")

        for cat_name, cat_url in categories:
            await self._scrape_category(page, cat_name, cat_url)
            await self._pause()

    async def _get_categories(self, page: Page) -> list[tuple[str, str]]:
        links = await page.locator(SELECTORS["category_links"]).all()
        seen: set[str] = set()
        result = []
        for link in links:
            href = (await link.get_attribute("href") or "").strip()
            text = (await link.inner_text()).strip()
            if not href or len(text) < 2:
                continue
            # Keep only grocery-like paths (at least /nl/X/Y)
            path = href.split("?")[0].rstrip("/")
            if path.count("/") < 3:
                continue
            url = self._abs_url(href)
            if url not in seen:
                seen.add(url)
                result.append((text, url))
        return result

    async def _scrape_category(self, page: Page, category: str, url: str):
        print(f"[{self.name}] → {category}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

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
