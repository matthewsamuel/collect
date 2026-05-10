"""
Delhaize scraper — https://www.delhaize.be

Product pages use /c/v2XXX URLs (SAP Commerce Cloud).
The category menu is behind a modal overlay that blocks Playwright clicks,
so we remove overlays via JS and dispatch the click event instead.

If selectors break, run:  python probe.py delhaize
"""
import re

from playwright.async_api import Page

from .base import BaseScraper

SELECTORS = {
    "product_card":   "[data-testid='product-block']",
    "name":           "[data-testid='product-block-product-name']",
    "brand":          "[data-testid='product-brand']",
    "price":          "[data-testid='product-block-price']",
    "promo":          "[data-testid='tag-promo']",
    "unit_price":     "[data-testid='product-block-price-per-unit']",
    "product_link":   "[data-testid='product-block-name-link']",
    "next_page": [
        "[data-testid='show-more-button']",
        "[aria-label='Volgende pagina']",
        "button:has-text('Meer')",
        "button:has-text('Volgende')",
        ".pagination-next",
    ],
}


class DelhaizeScraper(BaseScraper):
    name = "Delhaize"
    base_url = "https://www.delhaize.be"

    async def scrape(self, page: Page, max_categories: int | None = None):
        await page.goto(f"{self.base_url}/shoponline", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)
        await self._dismiss_cookies(page)

        categories = await self._get_categories(page)
        if max_categories:
            categories = categories[:max_categories]
        print(f"[{self.name}] {len(categories)} categories")

        for cat_name, cat_url in categories:
            await self._scrape_category(page, cat_name, cat_url)
            await self._pause()

    async def _get_categories(self, page: Page) -> list[tuple[str, str]]:
        # The page has overlay divs that intercept all Playwright pointer events.
        # Remove them and trigger the Categorieën menu button via JS dispatch.
        await page.evaluate("""
            () => document.querySelectorAll(
                "[data-testid*='overlay'], [data-testid*='modal']"
            ).forEach(el => el.remove())
        """)
        await page.evaluate("""
            () => {
                const btns = document.querySelectorAll("[data-testid='header-menu-toggle']");
                if (btns[0]) btns[0].dispatchEvent(
                    new MouseEvent('click', {bubbles: true, cancelable: true})
                );
            }
        """)
        await page.wait_for_timeout(2000)

        links = await page.locator("a[href*='/c/v2']").all()
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
        await page.wait_for_timeout(3000)

        page_num = 1
        extracted = 0
        while True:
            await self._scroll_to_bottom(page)
            cards = await page.locator(SELECTORS["product_card"]).all()
            new_cards = cards[extracted:]
            print(f"[{self.name}]   p{page_num}: {len(cards)} cards (+{len(new_cards)} new)")
            for card in new_cards:
                await self._extract(card, category, page.url)
            extracted = len(cards)

            if not await self._click_next_page(page, SELECTORS["next_page"]):
                break
            page_num += 1
            await self._pause()

    @staticmethod
    def _parse_price(text: str) -> str:
        """Handle Delhaize's split price format: '€\\n4\\n69' → '4.69'."""
        if not text:
            return ""
        text = text.replace("\xa0", " ").replace("€", "").strip()
        # Two separate digit groups (integer + decimal cents)
        parts = re.findall(r"\d+", text)
        if len(parts) == 2 and len(parts[1]) == 2:
            return f"{parts[0]}.{parts[1]}"
        return BaseScraper.clean_price(text)

    async def _extract(self, card, category: str, page_url: str):
        try:
            name = await self.safe_text(card, SELECTORS["name"])
            if not name:
                return
            try:
                href = (
                    await card.locator(SELECTORS["product_link"]).first.get_attribute("href")
                ) or page_url
            except Exception:
                href = page_url
            self.add_product(
                name=name,
                brand=await self.safe_text(card, SELECTORS["brand"]),
                price=self._parse_price(await self.safe_text(card, SELECTORS["price"])),
                promo_price=await self.safe_text(card, SELECTORS["promo"]),
                unit_price=await self.safe_text(card, SELECTORS["unit_price"]),
                category=category,
                url=self._abs_url(href),
            )
        except Exception:
            pass
