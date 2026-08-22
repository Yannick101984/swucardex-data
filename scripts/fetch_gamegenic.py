#!/usr/bin/env python3
"""
Synchronise accessories/catalogue.json avec les variantes live de gamegenic.com.

Phase 1 — Découverte automatique :
  Pagine l'API WooCommerce de gamegenic.com, détecte les nouveaux produits SWU
  dont le slug correspond au pattern d'un set connu, et met à jour products.json.

Phase 2 — Synchronisation des variantes :
  Pour chaque produit de products.json, récupère les variantes absentes,
  télécharge leurs images dans accessories/images/, met à jour catalogue.json.

Pour ajouter un nouveau set : ajouter son code dans KNOWN_SETS ci-dessous.
Le reste (découverte des produits, images, catalogue) est entièrement automatique.

Usage : python3 scripts/fetch_gamegenic.py --repo-root .
"""

from __future__ import annotations

import argparse
import html as html_module
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
DELAY = 1.5  # secondes entre chaque requête réseau

# ── À METTRE À JOUR pour chaque nouveau set SWU ──────────────────────────────
# Le script auto-découvre tous les produits dont le slug commence par
# "star-wars-unlimited-{setCode}-". Il suffit d'ajouter le nouveau code ici.
KNOWN_SETS: dict[str, str] = {
    "sor": "Spark of Rebellion",
    "shd": "Shadows of the Galaxy",
    "twi": "Twilight of the Republic",
    "jtl": "Jump to Lightspeed",
    "lof": "Legends of the Force",
    "sec": "Secrets of Power",
    "law": "A Lawless Time",
    "ash": "Ashes of the Empire",
}

# Mots-clés dans le slug → catégorie (ordre important : plus spécifique en premier)
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("card-back", "Sleeves"),
    ("sleeve", "Sleeves"),
    ("game-mat", "Playmat"),
    ("playmat", "Playmat"),
    ("deck-pod", "Deck Pod"),
    ("deckpod", "Deck Pod"),
    ("cardport", "Binder"),
    ("binder", "Binder"),
    ("album", "Album"),
    ("token", "Tokens"),
    ("storage", "Storage"),
    ("damage-pad", "Accessory"),
]


# ── Réseau ────────────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    """Retire les balises HTML et décode les entités HTML."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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
        match = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
        if not match:
            return None
        return json.loads(match.group(0))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def fetch_with_fallback(url: str) -> dict | list | None:
    data = fetch_json(url)
    if data is None:
        print(f"  [jina fallback] {url}", file=sys.stderr)
        time.sleep(DELAY)
        data = fetch_json(url, use_jina=True)
    return data


# ── Phase 1 : Découverte automatique ─────────────────────────────────────────

def fetch_all_swu_products_from_api() -> list[dict]:
    """
    Pagine l'API WooCommerce et retourne tous les produits dont le slug
    contient 'star-wars-unlimited'.
    """
    results: list[dict] = []
    page = 1
    print("→ Phase 1 : Découverte des produits SWU sur gamegenic.com…")
    while True:
        url = f"{STORE_API}?search=star+wars+unlimited&per_page=100&page={page}"
        data = fetch_with_fallback(url)
        if not data or not isinstance(data, list):
            break
        swu = [p for p in data if "star-wars-unlimited" in p.get("slug", "")]
        results.extend(swu)
        print(f"  page {page} : {len(data)} produits, {len(swu)} SWU retenus")
        if len(data) < 100:
            break
        page += 1
        time.sleep(DELAY)
    print(f"  Total : {len(results)} produits SWU trouvés.")
    return results


def detect_set_from_slug(slug: str) -> tuple[str, str] | None:
    """
    Retourne (setCode, setName) si le slug suit le pattern
    'star-wars-unlimited-{KNOWN_SET_CODE}-…'.
    """
    prefix = "star-wars-unlimited-"
    if not slug.startswith(prefix):
        return None
    candidate = slug[len(prefix):].split("-")[0].lower()
    if candidate in KNOWN_SETS:
        return candidate, KNOWN_SETS[candidate]
    return None


def detect_category_from_slug(slug: str) -> str:
    for keyword, cat in CATEGORY_KEYWORDS:
        if keyword in slug:
            return cat
    return "Accessory"


def clean_product_name(raw_name: str, set_name: str) -> str:
    """Extrait le nom du groupe produit depuis le nom WooCommerce brut."""
    # Supprimer le préfixe "Star Wars: Unlimited – " ou similaire
    name = re.sub(r"(?i)star\s*wars[™\s]*:\s*unlimited\s*[-–—]*\s*", "", raw_name).strip()
    # Supprimer le nom du set s'il est en tête
    if set_name and name.upper().startswith(set_name.upper()):
        name = name[len(set_name):].strip("– -").strip()
    return name or raw_name


def discover_new_products(
    api_products: list[dict],
    existing_slugs: set[str],
) -> list[dict]:
    """
    Retourne les nouvelles entrées à ajouter à products.json.
    Seuls les produits dont le slug correspond à un set connu sont traités ;
    les produits core à slug irrégulier restent gérés manuellement dans products.json.
    """
    new_entries: list[dict] = []
    for p in api_products:
        slug = p.get("slug", "")
        if slug in existing_slugs:
            continue
        set_info = detect_set_from_slug(slug)
        if not set_info:
            continue
        set_code, set_name = set_info
        category = detect_category_from_slug(slug)
        product_group = clean_product_name(p.get("name", slug), set_name)
        entry = {
            "setCode": set_code,
            "setName": set_name,
            "slug": slug,
            "productGroup": product_group,
            "category": category,
        }
        new_entries.append(entry)
        print(f"  + nouveau : {slug} ({set_code} / {product_group} / {category})")
    return new_entries


# ── Phase 2 : Synchronisation des variantes ───────────────────────────────────

def fetch_product_variants(slug: str) -> list[dict]:
    """Retourne la liste des variantes WooCommerce pour un slug produit."""
    url = f"{STORE_API}?slug={slug}"
    data = fetch_with_fallback(url)
    if not data or not isinstance(data, list) or len(data) == 0:
        print(f"  [SKIP] produit introuvable : {slug}", file=sys.stderr)
        return []

    product = data[0]
    variations = product.get("variations", [])

    if not variations:
        # Produit simple sans variantes
        img = ""
        images = product.get("images", [])
        if images:
            img = images[0].get("src", "")
        return [{"id": product.get("id"), "name": product.get("name", ""), "imageUrl": img}]

    results: list[dict] = []
    for var_item in variations:
        # L'API retourne soit un ID entier, soit un objet {"id": N, "attributes": [...]}
        if isinstance(var_item, dict):
            var_id = var_item.get("id")
            pre_attrs = var_item.get("attributes", [])
        else:
            var_id = var_item
            pre_attrs = []

        time.sleep(DELAY)
        var_data = fetch_with_fallback(f"{STORE_API}/{var_id}")

        img = ""
        var_name = ""
        detail_attrs: list[dict] = []

        if var_data and isinstance(var_data, dict):
            images = var_data.get("images", [])
            if images:
                img = images[0].get("src", "")
            var_name = var_data.get("name", "")
            detail_attrs = var_data.get("attributes", [])
        elif not pre_attrs:
            print(f"  [SKIP] variante {var_id} introuvable", file=sys.stderr)
            continue

        # Attribut : priorité aux données détaillées, sinon pré-extraites
        attr_param = ""
        attr_value = ""
        for attr in (detail_attrs or pre_attrs):
            # Formats possibles : {slug, value}, {attribute, value}, {name, value}
            slug = attr.get("slug") or attr.get("attribute") or ""
            name = attr.get("name", "")
            value = attr.get("value", "")
            if not slug and name:
                slug = name.lower().replace(" ", "_")
            if slug and value:
                attr_param = slug if slug.startswith("pa_") else f"pa_{slug}"
                attr_value = value
                break

        results.append({
            "id": var_id,
            "name": var_name,
            "imageUrl": img,
            "attrParam": attr_param,
            "attrValue": attr_value,
        })
    return results


def make_item_id(set_code: str, product_group: str, variant_slug: str) -> str:
    pg = product_group.lower().replace(" ", "-").replace("/", "-")
    return f"{set_code}_{variant_slug}_{pg}"


def slugify_variant(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def download_image(url: str, dest: Path) -> bool:
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


def _alphanum(s: str) -> str:
    """Minuscules, retire tout sauf lettres/chiffres, supprime numéro de fin de version."""
    s = re.sub(r"\s+\d+$", "", s.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def _word_set(s: str) -> set[str]:
    """Mots significatifs (min 2 chars), après remplacement hyphènes par espaces."""
    s = re.sub(r"[-]", " ", s.lower())
    return {w for w in re.sub(r"[^a-z0-9 ]", "", s).split() if len(w) >= 2}


def _levenshtein(a: str, b: str) -> int:
    """Distance d'édition simple (rejet rapide si différence de taille > 3)."""
    if abs(len(a) - len(b)) > 3:
        return 999
    dp = list(range(len(b) + 1))
    for ca in a:
        prev, dp[0] = dp[0], dp[0] + 1
        for j, cb in enumerate(b, 1):
            prev, dp[j] = dp[j], prev if ca == cb else 1 + min(prev, dp[j], dp[j - 1])
    return dp[len(b)]


def _find_fuzzy_match(
    set_code: str,
    product_group: str,
    clean_name: str,
    attr_value: str,
    catalogue: dict[str, dict],
) -> str | None:
    """
    Cherche un item existant dans le catalogue qui correspond au même produit
    mais avec un attr_value/nom différent (Gamegenic renomme parfois ses attributs).
    Retourne l'ID de l'item existant si trouvé, None sinon.
    """
    candidates = [
        item for item in catalogue.values()
        if item["setCode"] == set_code and item["productGroup"] == product_group
    ]
    if not candidates:
        return None

    target_raw = clean_name or attr_value

    # Variant générique sans discriminant (nom == groupe produit) avec plusieurs variants :
    # impossible de déterminer lequel matcher → skip
    if _alphanum(target_raw) == _alphanum(product_group) and len(candidates) > 1:
        return None

    target_an = _alphanum(target_raw)
    target_words = _word_set(target_raw)
    target_sorted = sorted(target_an)

    for candidate in candidates:
        cand_name = candidate.get("variantName", "")
        cand_an = _alphanum(cand_name)
        cand_words = _word_set(cand_name)

        # 1. Correspondance alphanum exacte (ignore tirets/espaces/numéros de version)
        if target_an == cand_an:
            return candidate["id"]

        # 2. L'un est contenu dans l'autre (ex: "Darth Maul" ⊂ "Darth Maul 2")
        if target_an and cand_an:
            if target_an in cand_an or cand_an in target_an:
                return candidate["id"]

        # 3. Mêmes caractères dans un ordre différent (ex: "C-3PO R2-D2" vs "R2-D2 C-3PO")
        if target_sorted == sorted(cand_an):
            return candidate["id"]

        # 4. Tous les mots du plus court sont dans le plus long
        # (ex: "Ahsoka Grievous" ⊆ "Ahsoka General Grievous",
        #      "Obi Wan Darth Maul" ⊆ "Obi-Wan Kenobi Darth Maul")
        if target_words and cand_words:
            shorter, longer = (
                (target_words, cand_words)
                if len(target_words) <= len(cand_words)
                else (cand_words, target_words)
            )
            if shorter and shorter.issubset(longer):
                return candidate["id"]

        # 5. Distance de Levenshtein ≤ 2 (ex: "millenium" vs "millennium")
        if target_an and cand_an and _levenshtein(target_an, cand_an) <= 2:
            return candidate["id"]

    # Si le produit n'a qu'une seule variante dans le catalogue,
    # c'est forcément le même article avec un attr_value différent
    if len(candidates) == 1:
        return candidates[0]["id"]

    return None


def sync_variants(
    products: list[dict],
    existing_catalogue_ids: dict[str, dict],
    images_dir: Path,
    dry_run: bool,
) -> tuple[list[dict], int]:
    """
    Traite tous les produits de products.json.
    Retourne (nouveaux_items, nb_backfills).
    Backfille aussi directUrl sur les items existants qui n'en ont pas encore.
    """
    new_items: list[dict] = []
    backfilled = 0

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
            variant_name = strip_html(var["name"])
            clean_name = re.sub(
                r"(?i)star\s*wars[™\s]*:?\s*unlimited\s*[™\s]*", "", variant_name
            ).strip()
            if clean_name.upper().startswith(product_group.upper()):
                clean_name = clean_name[len(product_group):].strip("– -").strip()

            # Calculer directUrl dès maintenant (utilisé aussi pour le backfill)
            attr_param = var.get("attrParam", "")
            attr_value = var.get("attrValue", "")

            # var_slug utilise la valeur d'attribut (format original des IDs)
            # Si pas d'attr_value, on tombe sur clean_name ou product_group
            if attr_value:
                var_slug = slugify_variant(attr_value)
            elif clean_name:
                var_slug = slugify_variant(clean_name)
            else:
                var_slug = slugify_variant(str(var["id"]))

            if not clean_name:
                clean_name = attr_value.replace("-", " ").title() if attr_value else product_group

            item_id = make_item_id(set_code, product_group, var_slug)
            direct_url = (
                f"{product_url}?attribute_{attr_param}={attr_value}"
                if attr_param and attr_value
                else product_url
            )

            if item_id in existing_catalogue_ids:
                existing = existing_catalogue_ids[item_id]
                current_direct = existing.get("directUrl")
                # Backfill si directUrl absent ou identique à l'URL générique
                if not current_direct or current_direct == existing.get("productPageUrl", ""):
                    existing["directUrl"] = direct_url
                    if direct_url != product_url:
                        backfilled += 1
                        print(f"   ↺ directUrl : {item_id}")
                    else:
                        print(f"   ✓ déjà présent (pas d'attribut) : {item_id}")
                else:
                    print(f"   ✓ déjà présent : {item_id}")
                continue

            # ID non trouvé : vérifier si un item existant correspond au même produit
            # (Gamegenic peut changer les attr_value d'un produit sans changer l'article)
            fuzzy = _find_fuzzy_match(
                set_code, product_group, clean_name, attr_value, existing_catalogue_ids
            )
            if fuzzy:
                fuzzy_item = existing_catalogue_ids[fuzzy]
                current_direct = fuzzy_item.get("directUrl")
                if not current_direct or current_direct == fuzzy_item.get("productPageUrl", ""):
                    fuzzy_item["directUrl"] = direct_url
                    if direct_url != product_url:
                        backfilled += 1
                        print(f"   ↺ directUrl (fuzzy→{fuzzy}) : {fuzzy}")
                    else:
                        print(f"   ✓ déjà présent (fuzzy, pas d'attribut) : {fuzzy}")
                else:
                    print(f"   ✓ déjà présent (fuzzy→{fuzzy})")
                continue

            # Si le nom du variant est générique (== groupe produit) et qu'il existe déjà
            # des variants pour ce groupe, on ignore ce variant ambigu
            existing_for_group = [
                i for i in existing_catalogue_ids.values()
                if i["setCode"] == set_code and i["productGroup"] == product_group
            ]
            if _alphanum(clean_name) == _alphanum(product_group) and existing_for_group:
                print(f"   ~ ignoré (variant générique ambigu) : {item_id}")
                continue

            img_filename = (
                f"{set_code}_{slug.replace('star-wars-unlimited-', '')}_{var_slug}.jpg"
            )
            img_dest = images_dir / img_filename
            img_url_github = BASE_IMG_URL + img_filename

            if var.get("imageUrl") and not dry_run:
                ok = download_image(var["imageUrl"], img_dest)
                if ok:
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
                "directUrl": direct_url,
                "acquired": False,
            }
            new_items.append(new_item)
            existing_catalogue_ids[item_id] = new_item
            print(f"   + ajouté : {item_id} ({clean_name})")

        time.sleep(DELAY)

    return new_items, backfilled


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Racine du repo swucardex-data")
    parser.add_argument("--dry-run", action="store_true", help="Ne pas écrire de fichiers")
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Sauter la phase de découverte (utiliser products.json tel quel)",
    )
    args = parser.parse_args()

    root = Path(args.repo_root)
    accessories_dir = root / "accessories"
    images_dir = accessories_dir / "images"
    products_path = accessories_dir / "products.json"
    catalogue_path = accessories_dir / "catalogue.json"

    if not products_path.exists():
        sys.exit(f"Fichier produits introuvable : {products_path}")

    products: list[dict] = json.loads(products_path.read_text())
    catalogue: list[dict] = json.loads(catalogue_path.read_text()) if catalogue_path.exists() else []
    existing_catalogue_ids: dict[str, dict] = {item["id"]: item for item in catalogue}
    existing_slugs: set[str] = {p["slug"] for p in products}
    products_changed = False

    # ── Phase 1 : Découverte ──────────────────────────────────────────────────
    if not args.skip_discovery:
        api_products = fetch_all_swu_products_from_api()
        new_products = discover_new_products(api_products, existing_slugs)
        if new_products:
            products.extend(new_products)
            existing_slugs.update(p["slug"] for p in new_products)
            products_changed = True
            print(f"  {len(new_products)} nouveau(x) produit(s) ajouté(s) à products.json.")
            if not args.dry_run:
                products_path.write_text(
                    json.dumps(products, indent=2, ensure_ascii=False) + "\n"
                )
        else:
            print("  Aucun nouveau produit détecté.")
    else:
        print("→ Phase 1 ignorée (--skip-discovery).")

    print()

    # ── Phase 2 : Synchronisation des variantes ───────────────────────────────
    print("→ Phase 2 : Synchronisation des variantes…")
    images_dir.mkdir(parents=True, exist_ok=True)
    new_items, backfilled = sync_variants(products, existing_catalogue_ids, images_dir, args.dry_run)

    print(f"\n{len(new_items)} nouvelle(s) variante(s) ajoutée(s), {backfilled} directUrl backfillé(s).")

    if (new_items or backfilled) and not args.dry_run:
        catalogue.extend(new_items)
        catalogue_path.write_text(
            json.dumps(catalogue, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"catalogue.json mis à jour ({len(catalogue)} items total).")
    elif not new_items and not backfilled:
        print("catalogue.json inchangé.")

    if args.dry_run and (new_items or backfilled or products_changed):
        print("\n[dry-run] Aucun fichier modifié.")


if __name__ == "__main__":
    main()
