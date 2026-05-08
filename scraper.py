#!/usr/bin/env python3
"""
Collect&Go product scraper
Haalt artikelnummers, namen en prijzen op via de interne API.
Output: products.json en products.csv
"""

import requests
import json
import csv
import time
import sys
from pathlib import Path

BASE_URL = "https://apip.collectandgo.be/gateway"
STORE_ID = "90004"
CATALOG_ID = "10429"
LANG_ID = "-1001"
PAGE_SIZE = 48
DELAY = 0.3  # seconden tussen requests

HEADERS = {
    "x-cg-apikey": "502b657c-624c-11eb-8024-f4d06b721e80",
    "accept": "application/json",
    "content-type": "application/json",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "origin": "https://www.collectandgo.be",
    "referer": "https://www.collectandgo.be/nl/home",
    "accept-language": "nl-BE,nl;q=0.9,en;q=0.8",
}

# Categorieën die overgeslagen worden (duplicaten / tijdelijk)
SKIP_CATEGORY_KEYWORDS = [
    "promo",
    "barbecue",
    "tijdelijk",
    "petshop winter",
    "alle baby",
    "online only",
]

session = requests.Session()
session.headers.update(HEADERS)


def get_category_hierarchy():
    url = (
        f"{BASE_URL}/ecomfoodb2c.eshop.wcscategoryhierarchysvc/v1"
        f"/store/{STORE_ID}/categoryHierarchy"
        f"?catalogId={CATALOG_ID}&langId={LANG_ID}"
    )
    r = session.get(url, timeout=20)
    r.raise_for_status()
    return r.json()["categoryHierarchy"]


def collect_depth2_categories(hierarchy):
    """Haalt depth-2 categorieën op (subcategorieën van hoofdcategorieën).
    Depth-2 scrapen is efficiënt: elk product staat in precies één subcategorie
    en de API retourneert ook kindproducten. Deduplicatie op artikel_id verwijdert overlappen.
    """
    seen_ids = set()
    result = []

    for top in hierarchy.get("categories", []):
        top_name = top.get("name", "")
        if any(kw in top_name.lower() for kw in SKIP_CATEGORY_KEYWORDS):
            continue

        subcats = top.get("categories") or []
        if not subcats:
            cat_id = top.get("id", "")
            if cat_id and cat_id not in seen_ids:
                seen_ids.add(cat_id)
                result.append({"id": cat_id, "name": top_name, "path": top_name})
            continue

        for sub in subcats:
            sub_name = sub.get("name", "")
            cat_id = sub.get("id", "")
            if any(kw in sub_name.lower() for kw in SKIP_CATEGORY_KEYWORDS):
                continue
            if cat_id and cat_id not in seen_ids:
                seen_ids.add(cat_id)
                result.append({
                    "id": cat_id,
                    "name": sub_name,
                    "path": f"{top_name} > {sub_name}",
                })

    return result


def fetch_products_for_category(category_id):
    products = []
    page = 1
    total_pages = None

    while True:
        url = (
            f"{BASE_URL}/ecomfoodb2c.eshop.wcsproductviewsearchsvc/v1"
            f"/store/{STORE_ID}/productview/byCategory/{category_id}"
        )
        params = {
            "searchSource": "E",
            "shopName": "CLPBE",
            "customFilterExpr": f"x_productstock:{CATALOG_ID}_*_Y",
            "pageSize": PAGE_SIZE,
            "pageNumber": page,
            "orderBy": 1,
            "catalogId": CATALOG_ID,
            "langId": LANG_ID,
        }
        try:
            r = session.post(url, json=params, timeout=20)
            if r.status_code == 405:
                # POST met JSON werkt niet, probeer POST met form-data
                r = session.post(url, data=params, timeout=20)
            if r.status_code == 405:
                # Fallback naar GET met query-parameters
                r = session.get(url, params=params, timeout=20)
            if r.status_code == 204:
                break
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    Fout bij categorie {category_id} pagina {page}: {e}", file=sys.stderr)
            break

        entries = data.get("catalogEntryView", [])
        products.extend(entries)

        if total_pages is None:
            total = int(data.get("recordSetTotal", 0))
            total_pages = max(1, -(-total // PAGE_SIZE))  # ceiling division

        if page >= total_pages or not entries:
            break

        page += 1
        time.sleep(DELAY)

    return products


def parse_product(raw, category_path):
    price_data = raw.get("xprice") or []
    base_price = None
    price_per_unit = None
    currency = "EUR"

    if price_data:
        p = price_data[0]
        base_price = p.get("basePrice")
        price_per_unit = p.get("basePriceVol")
        currency = p.get("currency", "EUR")

    promos = raw.get("promotions") or []
    promo_desc = "; ".join(p.get("description", "") for p in promos if p.get("description"))

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
            f"https://www.collectandgo.be/wcsstore/CollectAndGoSFAS/images/"
            + raw.get("thumbnail", "")
            if raw.get("thumbnail")
            else ""
        ),
        "unique_id": raw.get("uniqueID", ""),
    }


def save_json(products, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"JSON opgeslagen: {output_path} ({len(products)} producten)")


def save_csv(products, output_path):
    if not products:
        return
    fieldnames = list(products[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(products)
    print(f"CSV opgeslagen: {output_path} ({len(products)} producten)")


def main():
    output_dir = Path(__file__).parent
    json_path = output_dir / "products.json"
    csv_path = output_dir / "products.csv"

    print("Categorieën ophalen...")
    hierarchy = get_category_hierarchy()
    categories = collect_depth2_categories(hierarchy)
    print(f"  {len(categories)} categorieën gevonden (excl. promos)\n")

    all_products = {}  # gededupliceerd op artikel_id

    for i, cat in enumerate(categories, 1):
        print(f"[{i}/{len(categories)}] {cat['path']}...", end=" ", flush=True)
        raw_products = fetch_products_for_category(cat["id"])
        new_count = 0

        for raw in raw_products:
            parsed = parse_product(raw, cat["path"])
            article_id = parsed["artikel_id"] or parsed["unique_id"]
            if article_id and article_id not in all_products:
                all_products[article_id] = parsed
                new_count += 1

        print(f"{len(raw_products)} gevonden, {new_count} nieuw (totaal: {len(all_products)})")
        time.sleep(DELAY)

    product_list = list(all_products.values())
    print(f"\nTotaal unieke producten: {len(product_list)}")

    save_json(product_list, json_path)
    save_csv(product_list, csv_path)


if __name__ == "__main__":
    main()
