#!/usr/bin/env python3
"""
Belgian supermarket price scraper
Usage examples:
  python main.py                          # scrape all stores (headless)
  python main.py delhaize ah              # specific stores only
  python main.py --no-headless            # show browser window
  python main.py --max-categories 2       # quick test (2 categories/store)
  python main.py colruyt --delay 2.5      # slower / more polite
"""
import argparse
import asyncio

from scrapers.albert_heijn import AlbertHeijnScraper
from scrapers.carrefour import CarrefourScraper
from scrapers.colruyt import ColruytScraper
from scrapers.delhaize import DelhaizeScraper

SCRAPERS = {
    "colruyt":  ColruytScraper,
    "delhaize": DelhaizeScraper,
    "carrefour": CarrefourScraper,
    "ah":       AlbertHeijnScraper,
}


async def main():
    parser = argparse.ArgumentParser(
        description="Scrape Belgian online supermarkets and save prices to CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "stores",
        nargs="*",
        choices=[*SCRAPERS.keys(), "all"],
        default=["all"],
        metavar="STORE",
        help=f"Stores to scrape: {', '.join(SCRAPERS)} or 'all' (default)",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        default=True,
        help="Show the browser window (useful for debugging selectors)",
    )
    parser.add_argument(
        "--max-categories",
        type=int,
        metavar="N",
        default=None,
        help="Only scrape first N categories per store (for quick tests)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        metavar="SEC",
        default=1.5,
        help="Base delay between requests in seconds (default: 1.5)",
    )
    args = parser.parse_args()

    targets = list(SCRAPERS.keys()) if "all" in args.stores else args.stores

    for key in targets:
        cls = SCRAPERS[key]
        scraper = cls(headless=args.headless, delay=args.delay)
        print(f"\n{'━' * 55}")
        print(f"  {scraper.name}  —  {scraper.base_url}")
        print(f"{'━' * 55}")
        try:
            await scraper.run(max_categories=args.max_categories)
        except Exception as exc:
            print(f"[{scraper.name}] Skipped — {exc}")

    print("\nDone. CSV files are in ./data/")


if __name__ == "__main__":
    asyncio.run(main())
