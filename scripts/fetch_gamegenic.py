#!/usr/bin/env python3
"""Synchronise accessories/catalogue.json avec les variantes live de gamegenic.com.

Lit accessories/products.json pour la liste des produits connus.
Pour chaque produit, interroge l'API WooCommerce Store de gamegenic.com
et ajoute au catalogue les variantes absentes + télécharge leurs images.

Usage : python3 scripts/fetch_gamegenic.py --repo-root .
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STORE_API = "https://www.gamegenic.com/wp-json/wc/store/products"
JINA_PREFIX = "https://r.jina.ai/"
BASE_IMG_URL = "https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/accessories/images/"
USER_AGENT = "Mozilla/5.0 (compatible; swucardex-gamegenic-bot/1.0)"
DELAY = 1.5  # secondes entre chaque requête


def fetch_json(url: str, use_jina: bool = False) -> dict | list | None:
    """Récupère du JSON depuis une URL, avec fallback via r.jina.ai."""
    target = (JINA_PREFIX + url) if use_jina else url
    req = urllib.request.Request(
        target,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.gamegenic.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        # Jina peut ajouter un en-tête texte avant le JSON — on extrait le JSON
        match = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
        if not match:
            return None
        return json.loads(match.group(0))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def fetch_product_variants(slug: str) -> list[dict]:
    """Retourne la liste des variantes WooCommerce pour un slug produit."""
    url = f"{STORE_API}?slug={slug}"
    data = fetch_json(url)
    if data is None:
        print(f"  [direct KO] tentative via jina pour {slug}", file=sys.stderr)
        time.sleep(DELAY)
        data = fetch_json(url, use_jina=True)
    if not data or not isinstance(data, list) or len(data) == 0:
        print(f"  [SKIP] produit introuvable : {slug}", file=sys.stderr)
        return []

    product = data[0]
    variations = product.get("variations", [])
    if not variations:
        # Produit sans variantes → c'est lui-même la seule "variante"
        img = ""
        images = product.get("images", [])
        if images:
            img = images[0].get("src", "")
        return [{"id": product.get("id"), "name": product.get("name", ""), "imageUrl": img}]

    results = []
    for var_id in variations:
        time.sleep(DELAY)
        var_url = f"{STORE_API}/{var_id}"
        var_data = fetch_json(var_url)
        if var_data is None:
            var_data = fetch_json(var_url, use_jina=True)
        if not var_data or not isinstance(var_data, dict):
            print(f"  [SKIP] variante {var_id} introuvable", file=sys.stderr)
            continue
        img = ""
        images = var_data.get("images", [])
        if images:
            img = images[0].get("src", "")
        results.append({
            "id": var_id,
            "name": var_data.get("name", ""),
            "imageUrl": img,
        })
    return results


def make_item_id(set_code: str, product_group: str, variant_slug: str) -> str:
    pg = product_group.lower().replace(" ", "-").replace("/", "-")
    return f"{set_code}_{variant_slug}_{pg}"


def slugify_variant(name: str) -> str:
    """Convertit un nom de variante en slug stable."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def download_image(url: str, dest: Path) -> bool:
    """Télécharge une image et la sauvegarde."""
    if dest.exists():
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as exc:
        print(f"  [IMG KO] {url} → {exc}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Racine du repo swucardex-data")
    parser.add_argument("--dry-run", action="store_true", help="Ne pas écrire de fichiers")
    args = parser.parse_args()

    root = Path(args.repo_root)
    accessories_dir = root / "accessories"
    images_dir = accessories_dir / "images"
    products_path = accessories_dir / "products.json"
    catalogue_path = accessories_dir / "catalogue.json"

    if not products_path.exists():
        sys.exit(f"Fichier produits introuvable : {products_path}")

    products: list[dict] = json.loads(products_path.read_text())
    catalogue: list[dict] = []
    if catalogue_path.exists():
        catalogue = json.loads(catalogue_path.read_text())

    # Index des items existants par id
    existing: dict[str, dict] = {item["id"]: item for item in catalogue}
    added = 0
    images_dir.mkdir(parents=True, exist_ok=True)

    for product in products:
        set_code = product["setCode"]
        set_name = product["setName"]
        slug = product["slug"]
        product_group = product["productGroup"]
        category = product["category"]
        product_url = f"https://www.gamegenic.com/product/{slug}/"

        print(f"→ {set_code} / {product_group} ({slug})")
        variants = fetch_product_variants(slug)

        for var in variants:
            variant_name = var["name"]
            # Nettoyer le nom : retirer le préfixe générique du produit si présent
            clean_name = re.sub(
                r"(?i)star\s*wars[™\s]*:?\s*unlimited\s*", "", variant_name
            ).strip()
            # Retirer le nom du productGroup du début si présent
            if clean_name.upper().startswith(product_group.upper()):
                clean_name = clean_name[len(product_group):].strip("– -").strip()
            if not clean_name:
                clean_name = product_group

            var_slug = slugify_variant(clean_name or str(var["id"]))
            item_id = make_item_id(set_code, product_group, var_slug)

            if item_id in existing:
                print(f"   ✓ déjà présent : {item_id}")
                continue

            # Téléchargement image
            img_filename = f"{set_code}_{slug.replace('star-wars-unlimited-', '')}_{var_slug}.jpg"
            img_dest = images_dir / img_filename
            img_url_github = BASE_IMG_URL + img_filename

            if var.get("imageUrl") and not args.dry_run:
                if download_image(var["imageUrl"], img_dest):
                    print(f"   ↓ image : {img_filename}")
                else:
                    img_url_github = var.get("imageUrl", "")

            new_item = {
                "id": item_id,
                "setCode": set_code,
                "setName": set_name,
                "productGroup": product_group,
                "category": category,
                "variantName": clean_name,
                "variantSlug": var_slug,
                "imageURL": img_url_github,
                "productPageUrl": product_url,
                "acquired": False,
            }
            catalogue.append(new_item)
            existing[item_id] = new_item
            added += 1
            print(f"   + ajouté : {item_id} ({clean_name})")

        time.sleep(DELAY)

    print(f"\n{added} nouvelles variante(s) ajoutée(s).")

    if added > 0 and not args.dry_run:
        catalogue_path.write_text(json.dumps(catalogue, indent=2, ensure_ascii=False) + "\n")
        print(f"catalogue.json mis à jour ({len(catalogue)} items total).")
    elif added == 0:
        print("catalogue.json inchangé.")


if __name__ == "__main__":
    main()
