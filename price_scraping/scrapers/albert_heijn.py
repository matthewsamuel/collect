"""
Albert Heijn Belgium scraper — https://www.ah.be

AH uses consistent `data-testhook` attributes across their NL and BE sites,
making selectors relatively stable. Falls back to class-name patterns.

If selectors break, run:  python probe.py ah
"""
from playwright.async_api import Page

from .base import BaseScraper

SELECTORS = {
    "category_links": (
        "nav a[href*='/producten/'], nav a[href*='/categorie/'], "
        "a[data-testhook='category-link'], [class*='CategoryNav' i] a, "
        "[class*='category-nav' i] a, a[href*='/shop/']"
    ),
    "product_card": (
        "[data-testhook='product-card'], "
        "[class*='ProductCard' i], [class*='product-card' i], "
        "li[class*='product' i], article[class*='product' i]"
    ),
    "name": (
        "[data-testhook='product-title'], "
        "[class*='title' i], h3, h2"
    ),
    "brand": (
        "[data-testhook='product-sub-title'], "
        "[class*='brand' i], [class*='sub-title' i]"
    ),
    "price": (
        "[data-testhook='price-amount'], "
        "[class*='price' i]:not([class*='unit' i]):not([class*='per' i])"
    ),
    "promo": (
        "[data-testhook='promotion-price'], "
        "[class*='promo' i], [class*='discount' i]"
    ),
    "unit_price": (
        "[data-testhook='product-unit-size'], "
        "[class*='unit' i][class*='price' i], [class*='per-unit' i]"
    ),
    "next_page": [
        "[data-testhook='pagination-next']",
        "[aria-label='Volgende pagina']",
        "button:has-text('Volgende')",
        "button:has-text('Meer producten')",
        ".pagination-next",
        "a[rel='next']",
    ],
}


class AlbertHeijnScraper(BaseScraper):
    name = "Albert Heijn"
    base_url = "https://www.ah.be"

    async def scrape(self, page: Page, max_categories: int | None = None):
        # Warm up on homepage first — /producten returns 403 without prior session
        await page.goto(f"{self.base_url}/", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        # Cookie popup uses a <dialog> that blocks pointer events; dismiss via JS
        await page.evaluate("""
            () => {
                const btn = document.querySelector("[data-testid='accept-cookies']");
                if (btn) btn.click();
            }
        """)
        await page.wait_for_timeout(1000)

        resp = await page.goto(f"{self.base_url}/producten", wait_until="domcontentloaded", timeout=30_000)
        if resp and resp.status == 403:
            print(f"[{self.name}] 403 on /producten — site requires a real browser (run with --no-headless)")
            return
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
            if not href or not text:
                continue
            url = self._abs_url(href)
            if url not in seen:
                seen.add(url)
                result.append((text, url))
        return result

    async def _scrape_category(self, page: Page, category: str, url: str):
        print(f"[{self.name}] → {category}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

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
