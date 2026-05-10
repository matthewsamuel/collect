#!/usr/bin/env python3
"""
Selector probe — visit a supermarket site and report candidate CSS selectors
for product cards, prices, and category navigation.

Run with the browser visible so you can inspect elements manually too.

Usage:
  python probe.py colruyt
  python probe.py delhaize
  python probe.py carrefour
  python probe.py ah
"""
import argparse
import asyncio

from playwright.async_api import async_playwright

TARGETS = {
    "colruyt":  "https://www.collectandgo.be/nl",
    "delhaize": "https://www.delhaize.be/nl/shoponline",
    "carrefour": "https://www.carrefour.be/nl",
    "ah":       "https://www.ah.be/producten",
}

# Injected into the page via evaluate(); returns JSON-serialisable data
_PROBE_JS = """
() => {
    function score(el) {
        const cls = (el.className || '').toLowerCase();
        const id  = (el.id || '').toLowerCase();
        const tag = el.tagName.toLowerCase();
        let s = 0;
        for (const kw of ['product', 'item', 'tile', 'card']) {
            if (cls.includes(kw) || id.includes(kw)) s += 10;
        }
        if (['article', 'li'].includes(tag)) s += 3;
        if (el.hasAttribute('data-testhook')) s += 5;
        return s;
    }

    function toSel(el) {
        const cls = Array.from(el.classList).slice(0, 3).join('.');
        return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
    }

    const all = Array.from(document.querySelectorAll('*'));

    // Best product card candidates
    const cards = all
        .filter(el => score(el) > 5)
        .sort((a, b) => score(b) - score(a))
        .slice(0, 6)
        .map(el => ({
            selector: toSel(el),
            testhook: el.getAttribute('data-testhook') || '',
            count:    document.querySelectorAll(toSel(el)).length,
            preview:  el.innerText.slice(0, 100).replace(/\\s+/g, ' '),
        }));

    // Elements that look like prices
    const prices = all
        .filter(el => {
            const t = (el.innerText || '').trim();
            return /€\\s*\\d/.test(t) && t.length < 25 && el.children.length === 0;
        })
        .slice(0, 8)
        .map(el => ({
            selector: toSel(el),
            testhook: el.getAttribute('data-testhook') || '',
            text: el.innerText.trim(),
        }));

    // Nav links
    const navLinks = Array.from(document.querySelectorAll('nav a'))
        .slice(0, 12)
        .map(a => ({ href: a.href, text: a.innerText.trim().slice(0, 50) }));

    return { cards, prices, navLinks };
}
"""


async def probe(store: str):
    url = TARGETS[store]
    print(f"\nProbing '{store}' → {url}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        # Give cookie banners a moment to appear
        await page.wait_for_timeout(3000)

        result = await page.evaluate(_PROBE_JS)

        print("═" * 60)
        print("  PRODUCT CARD CANDIDATES")
        print("═" * 60)
        for c in result["cards"]:
            hook = f"  [data-testhook='{c['testhook']}']" if c["testhook"] else ""
            print(f"  {c['selector']!r:<55} ×{c['count']}{hook}")
            print(f"    preview: {c['preview']!r}")

        print("\n" + "═" * 60)
        print("  PRICE ELEMENTS")
        print("═" * 60)
        for p in result["prices"]:
            hook = f"  [data-testhook='{p['testhook']}']" if p["testhook"] else ""
            print(f"  {p['selector']!r:<50}  {p['text']!r}{hook}")

        print("\n" + "═" * 60)
        print("  NAVIGATION LINKS (first 12)")
        print("═" * 60)
        for ln in result["navLinks"]:
            print(f"  {ln['text']!r:<35}  {ln['href']}")

        print(
            "\nBrowser is open for manual inspection.\n"
            "Right-click any element → Inspect to find selectors.\n"
            "Close the browser window to exit."
        )
        await browser.wait_for_event("disconnected")


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("store", choices=list(TARGETS.keys()), help="Store to probe")
    args = parser.parse_args()
    await probe(args.store)


if __name__ == "__main__":
    asyncio.run(main())
