#!/usr/bin/env python3
"""
Collect&Go product scraper
Haalt artikelnummers, namen en prijzen op via de interne API.

Kenmerken:
  - Verse wcauthtoken via Playwright (elke TOKEN_REFRESH_INTERVAL verzoeken)
  - Resume: slaat voortgang op in progress.json; hervat bij herstart
  - Trage rate: DELAY seconden tussen verzoeken om Imperva-blokkering te voorkomen
  - Dedupliceert op artikel_id
  - Output: products.json + products.csv

Gebruik:
  python3 scraper.py          # volledig scrapen (of hervatten)
  python3 scraper.py --reset  # begin opnieuw (wist voortgang)
"""

import requests
import json
import csv
import time
import sys
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://apip.collectandgo.be/gateway"
STORE_ID = "90004"
CATALOG_ID = "10429"
LANG_ID = "-1001"
PAGE_SIZE = 48
DELAY = 2.0                  # seconden tussen API-verzoeken (bewust traag)
TOKEN_REFRESH_INTERVAL = 20  # vernieuw token elke N verzoeken

HEADERS = {
    "x-cg-apikey": "502b657c-624c-11eb-8024-f4d06b721e80",
    "accept": "application/json",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "referer": "https://www.collectandgo.be/nl/home",
}

SKIP_CATEGORY_KEYWORDS = [
    "promo", "barbecue", "tijdelijk",
    "petshop winter", "alle baby", "online only",
]

session = requests.Session()
session.headers.update(HEADERS)
request_count = 0


def refresh_token() -> None:
    """Laad de site via Playwright en onderschep de verse wcauthtoken."""
    global request_count
    token = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=HEADERS["user-agent"],
            locale="nl-BE",
        )
        page = context.new_page()

        def on_request(req):
            nonlocal token
            t = req.headers.get("wcauthtoken")
            if t:
                token = t

        page.on("request", on_request)
        try:
            page.goto(
                "https://www.collectandgo.be/nl/assortiment/fruit",
                timeout=45000,
            )
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(5000)
        except Exception:
            pass
        context.close()
        browser.close()

    if token:
        session.headers.update({"wcauthtoken": token})
        request_count = 0
        print(f"  [token vernieuwd]")
    else:
        print("  [waarschuwing: geen token gevonden]", file=sys.stderr)


def api_get(url: str) -> dict | None:
    """GET met automatische token-vernieuwing en rate limiting."""
    global request_count
    if request_count > 0 and request_count % TOKEN_REFRESH_INTERVAL == 0:
        print(f"\n  [preventieve token-vernieuwing na {request_count} verzoeken]")
        refresh_token()

    time.sleep(DELAY)
    try:
        r = session.get(url, timeout=20)
        request_count += 1
        if r.status_code == 405:
            print(f"\n  [405 – token vernieuwen en opnieuw proberen]", file=sys.stderr)
            refresh_token()
            time.sleep(DELAY)
            r = session.get(url, timeout=20)
            request_count += 1
        if r.status_code == 204:
            return {}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"\n  [fout: {e}]", file=sys.stderr)
        return None


def get_category_hierarchy() -> dict:
    url = (
        f"{BASE_URL}/ecomfoodb2c.eshop.wcscategoryhierarchysvc/v1"
        f"/store/{STORE_ID}/categoryHierarchy"
        f"?catalogId={CATALOG_ID}&langId={LANG_ID}"
    )
    data = api_get(url)
    if not data:
        raise RuntimeError("Categorieën ophalen mislukt")
    return data["categoryHierarchy"]


def collect_depth2_categories(hierarchy: dict) -> list[dict]:
    seen: set[str] = set()
    result = []
    for top in hierarchy.get("categories", []):
        top_name = top.get("name", "")
        if any(kw in top_name.lower() for kw in SKIP_CATEGORY_KEYWORDS):
            continue
        subcats = top.get("categories") or []
        targets = subcats if subcats else [top]
        for sub in targets:
            sub_name = sub.get("name", "")
            cat_id = sub.get("id", "")
            if any(kw in sub_name.lower() for kw in SKIP_CATEGORY_KEYWORDS):
                continue
            if cat_id and cat_id not in seen:
                seen.add(cat_id)
                path = f"{top_name} > {sub_name}" if subcats else top_name
                result.append({"id": cat_id, "name": sub_name, "path": path})
    return result


def fetch_products_for_category(cat_id: str) -> list[dict]:
    products = []
    page_num = 1
    total_pages = None

    while True:
        url = (
            f"{BASE_URL}/ecomfoodb2c.eshop.wcsproductviewsearchsvc/v1"
            f"/store/{STORE_ID}/productview/byCategory/{cat_id}"
            f"?searchSource=E&shopName=CLPBE"
            f"&customFilterExpr=x_productstock%3A{CATALOG_ID}_*_Y"
            f"&pageSize={PAGE_SIZE}&pageNumber={page_num}"
            f"&orderBy=1&catalogId={CATALOG_ID}&langId={LANG_ID}"
        )
        data = api_get(url)
        if data is None:
            break
        if not data:  # 204
            break

        entries = data.get("catalogEntryView", [])
        products.extend(entries)

        if total_pages is None:
            total = int(data.get("recordSetTotal", 0))
            total_pages = max(1, -(-total // PAGE_SIZE))

        if page_num >= total_pages or not entries:
            break
        page_num += 1

    return products


def parse_product(raw: dict, category_path: str) -> dict:
    price_data = raw.get("xprice") or []
    base_price = price_per_unit = None
    currency = "EUR"
    if price_data:
        p = price_data[0]
        base_price = p.get("basePrice")
        price_per_unit = p.get("basePriceVol")
        currency = p.get("currency", "EUR")

    promos = raw.get("promotions") or []
    promo_desc = "; ".join(
        p.get("description", "") for p in promos if p.get("description")
    )

    return {
        "artikel_id": raw.get("x_techArticleId", ""),
        "part_number": raw.get("partNumber", ""),
        "naam": raw.get("name", ""),
        "lange_naam": raw.get("productLongName", ""),
        "prijs": base_price,
        "prijs_per_eenheid": price_per_unit,
        "valuta": currency,
        "beschikbaar": raw.get("isProductAvailable", ""),
        "koopbaar": raw.get("buyable", ""),
        "promo": promo_desc,
        "categorie": category_path,
        "thumbnail_url": (
            "https://www.collectandgo.be/wcsstore/CollectAndGoSFAS/images/"
            + raw.get("thumbnail", "")
            if raw.get("thumbnail") else ""
        ),
        "unique_id": raw.get("uniqueID", ""),
    }


def save_outputs(products: dict, output_dir: Path) -> None:
    product_list = list(products.values())
    json_path = output_dir / "products.json"
    csv_path = output_dir / "products.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(product_list, f, ensure_ascii=False, indent=2)

    if product_list:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(product_list[0].keys()))
            writer.writeheader()
            writer.writerows(product_list)

    print(f"  JSON: {json_path} ({len(product_list)} producten)")
    print(f"  CSV:  {csv_path} ({len(product_list)} producten)")


def load_progress(progress_path: Path) -> tuple[dict, set]:
    """Laad eerder gescrapte producten en de al-verwerkte categorie-ID's."""
    if not progress_path.exists():
        return {}, set()
    with open(progress_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("products", {}), set(data.get("done_ids", []))


def save_progress(
    progress_path: Path, products: dict, done_ids: set
) -> None:
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(
            {"products": products, "done_ids": list(done_ids)},
            f,
            ensure_ascii=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Begin opnieuw (wist voortgang)",
    )
    args = parser.parse_args()

    output_dir = Path(__file__).parent
    progress_path = output_dir / "progress.json"

    if args.reset and progress_path.exists():
        progress_path.unlink()
        print("Voortgang gewist.")

    print("Verse wcauthtoken ophalen via Playwright...")
    refresh_token()

    print("\nCategorieën ophalen...")
    hierarchy = get_category_hierarchy()
    categories = collect_depth2_categories(hierarchy)
    print(f"  {len(categories)} categorieën gevonden\n")

    all_products, done_ids = load_progress(progress_path)
    if done_ids:
        print(f"  Hervatten: {len(done_ids)} categorieën al verwerkt, "
              f"{len(all_products)} producten geladen.\n")

    for i, cat in enumerate(categories, 1):
        if cat["id"] in done_ids:
            continue

        print(f"[{i}/{len(categories)}] {cat['path']}...", end=" ", flush=True)
        raw_products = fetch_products_for_category(cat["id"])
        new_count = 0

        for raw in raw_products:
            parsed = parse_product(raw, cat["path"])
            key = parsed["artikel_id"] or parsed["unique_id"]
            if key and key not in all_products:
                all_products[key] = parsed
                new_count += 1

        done_ids.add(cat["id"])
        print(
            f"{len(raw_products)} gevonden, {new_count} nieuw "
            f"(totaal: {len(all_products)})"
        )
        save_progress(progress_path, all_products, done_ids)

    print(f"\nKlaar. Totaal unieke producten: {len(all_products)}")
    print("Opslaan...")
    save_outputs(all_products, output_dir)

    # Voortgangsbestand opruimen na succesvolle voltooiing
    if progress_path.exists():
        progress_path.unlink()


if __name__ == "__main__":
    main()
