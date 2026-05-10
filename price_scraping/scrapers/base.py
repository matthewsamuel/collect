import asyncio
import csv
import random
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Locator, Page, async_playwright

DATA_DIR = Path(__file__).parent.parent / "data"


class BaseScraper(ABC):
    name: str
    base_url: str

    def __init__(self, headless: bool = True, delay: float = 1.5):
        self.headless = headless
        self.delay = delay
        self.products: list[dict[str, Any]] = []

    # ── Timing ────────────────────────────────────────────────────────────

    async def _pause(self, factor: float = 1.0):
        await asyncio.sleep(self.delay * factor + random.uniform(0.2, 0.6))

    # ── Cookie banners ────────────────────────────────────────────────────

    async def _dismiss_cookies(self, page: Page) -> bool:
        for sel in [
            "#onetrust-accept-btn-handler",
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "button:has-text('Alles accepteren')",
            "button:has-text('Accepteer alles')",
            "button:has-text('Aanvaarden')",
            "button:has-text('Ik ga akkoord')",
            "button:has-text('Accept all')",
            "button:has-text('Akkoord')",
            "[data-test='cookie-consent-accept']",
            "[aria-label*='accept' i]",
            ".js-cookie-accept",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await page.wait_for_timeout(800)
                    return True
            except Exception:
                continue
        return False

    # ── Scrolling / pagination ─────────────────────────────────────────────

    async def _scroll_to_bottom(self, page: Page, settle_ms: int = 1200):
        """Scroll until no new content loads (handles infinite scroll)."""
        prev = -1
        while True:
            cur = await page.evaluate("document.body.scrollHeight")
            if cur == prev:
                break
            prev = cur
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(settle_ms)

    async def _click_next_page(self, page: Page, selectors: list[str]) -> bool:
        """Click a pagination next-button. Returns True if a button was found and clicked."""
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000) and await btn.is_enabled():
                    await btn.click()
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass  # click happened; content may still be loading
                    return True
            except Exception:
                continue
        return False

    # ── Main entry point ───────────────────────────────────────────────────

    async def run(self, max_categories: int | None = None) -> list[dict]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="nl-BE",
                extra_http_headers={"Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8"},
            )
            page = await ctx.new_page()
            try:
                await self.scrape(page, max_categories=max_categories)
            except Exception as exc:
                print(f"[{self.name}] Fatal error: {exc}")
                raise
            finally:
                await browser.close()
        self.save_csv()
        return self.products

    @abstractmethod
    async def scrape(self, page: Page, max_categories: int | None = None):
        pass

    # ── Data helpers ───────────────────────────────────────────────────────

    def add_product(self, **kwargs):
        row: dict[str, Any] = {
            "store": self.name,
            "category": "",
            "name": "",
            "brand": "",
            "price": "",
            "promo_price": "",
            "unit_price": "",
            "unit": "",
            "url": "",
            "scraped_at": datetime.now().isoformat(),
        }
        row.update(kwargs)
        self.products.append(row)

    def save_csv(self):
        if not self.products:
            print(f"[{self.name}] No products scraped.")
            return
        slug = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
        path = DATA_DIR / f"{slug}.csv"
        fields = [
            "store", "category", "name", "brand",
            "price", "promo_price", "unit_price", "unit",
            "url", "scraped_at",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.products)
        print(f"[{self.name}] {len(self.products):,} products → {path}")

    @staticmethod
    def clean_price(text: str) -> str:
        if not text:
            return ""
        text = text.replace("\xa0", " ").replace(",", ".").replace("€", "").strip()
        m = re.search(r"\d+\.?\d*", text)
        return m.group() if m else ""

    @staticmethod
    async def safe_text(locator: Locator, selector: str, timeout: int = 500) -> str:
        """Inner text of the first element matching selector, or '' on failure."""
        try:
            return (await locator.locator(selector).first.inner_text(timeout=timeout)).strip()
        except Exception:
            return ""

    @staticmethod
    async def first_text(locator: Locator, *selectors: str) -> str:
        """Try each selector in turn; return first non-empty result."""
        for sel in selectors:
            try:
                text = (await locator.locator(sel).first.inner_text(timeout=400)).strip()
                if text:
                    return text
            except Exception:
                continue
        return ""

    def _abs_url(self, href: str) -> str:
        return href if href.startswith("http") else self.base_url + href
