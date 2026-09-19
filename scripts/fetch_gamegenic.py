#!/usr/bin/env python3
"""
Synchronise accessories/catalogue.json avec les variantes live de gamegenic.com.

Détection des sets — entièrement automatique :
  Chaque produit SWU sur gamegenic.com est rangé dans une catégorie WooCommerce
  imbriquée sous "starwarsunlimited/{slug-du-set}" (ex: "starwarsunlimited/homeworlds"),
  dont le nom humain est fourni par Gamegenic lui-même (ex: "Homeworlds").
  Un seul appel API (`?category=starwarsunlimited`) suffit à tout découvrir :
  aucun set à ajouter à la main, plus jamais.

  LEGACY_CODE_BY_SLUG ne sert qu'à conserver les identifiants courts déjà utilisés
  par les sets ajoutés avant ce mécanisme (sor, shd… ash). Elle ne doit plus jamais
  grandir : les nouveaux sets utilisent directement le slug de catégorie Gamegenic.

  L'ordre d'affichage dans l'app (le plus récent en premier) est lui aussi automatique :
  chaque nouvel item reçoit un champ `setOrder` (AAAAMM) déduit de la date d'upload
  de son image sur gamegenic.com.

Rapidité :
  Le nom+la valeur de chaque variante sont déjà présents dans la réponse de l'appel
  unique de découverte (`variations[].attributes`). Un item déjà connu ne déclenche
  donc AUCUNE requête réseau supplémentaire — seuls les items réellement nouveaux
  (nom + image manquants) déclenchent un appel `products/{id}` + téléchargement.
  Un run sans nouveauté = 1 seul appel réseau au total.

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
DELAY = 1.5  # secondes entre deux requêtes réseau (uniquement pour les items nouveaux)

# Figée : uniquement les sets ajoutés avant l'auto-détection par catégorie.
# Ne plus jamais ajouter de ligne ici — les nouveaux sets utilisent le slug Gamegenic tel quel.
LEGACY_CODE_BY_SLUG: dict[str, str] = {
    "spark-of-rebellion": "sor",
    "shadows-of-the-galaxy": "shd",
    "twilight-of-the-republic": "twi",
    "jump-to-lightspeed": "jtl",
    "legends-of-the-force": "lof",
    "secrets-of-power": "sec",
    "a-lawless-time": "law",
    "ashes-of-the-empire": "ash",
    "swu-core-products": "core",
    "homeworlds": "hmw",
}

SET_CATEGORY_RE = re.compile(r"/product-category/starwarsunlimited/([a-z0-9-]+)/?$")

# Mots-clés dans le slug → catégorie d'accessoire (ordre important : plus spécifique en premier)
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


def fetch_all_swu_products() -> list[dict]:
    """
    Un seul point d'entrée réseau (paginé par sécurité) : tous les produits
    rangés dans la catégorie WooCommerce "starwarsunlimited". Chaque produit
    porte déjà sa catégorie de set imbriquée + ses variantes + ses images.
    """
    results: list[dict] = []
    page = 1
    print("→ Découverte des produits SWU sur gamegenic.com…")
    while True:
        url = f"{STORE_API}?category=starwarsunlimited&per_page=100&page={page}"
        data = fetch_with_fallback(url)
        if not data or not isinstance(data, list):
            break
        results.extend(data)
        print(f"  page {page} : {len(data)} produits")
        if len(data) < 100:
            break
        page += 1
        time.sleep(DELAY)
    print(f"  Total : {len(results)} produits SWU trouvés.")
    return results


# ── Détection automatique du set ──────────────────────────────────────────────

def detect_set_category(categories: list[dict]) -> tuple[str, str] | None:
    """
    Retourne (categorySlug, setName) à partir de la catégorie WooCommerce
    imbriquée directement sous "starwarsunlimited/" (ex: "homeworlds" / "Homeworlds").
    """
    for c in categories or []:
        m = SET_CATEGORY_RE.search(c.get("link", ""))
        if m and c.get("slug") != "starwarsunlimited":
            return c["slug"], html_module.unescape(c.get("name", "")).strip()
    return None


def resolve_set_code(category_slug: str) -> str:
    return LEGACY_CODE_BY_SLUG.get(category_slug, category_slug)


def extract_release_month(image_url: str) -> int | None:
    """AAAAMM déduit de la date d'upload de l'image (chemin /uploads/AAAA/MM/)."""
    m = re.search(r"/uploads/(\d{4})/(\d{2})/", image_url or "")
    return int(m.group(1)) * 100 + int(m.group(2)) if m else None


def detect_category_from_slug(slug: str) -> str:
    for keyword, cat in CATEGORY_KEYWORDS:
        if keyword in slug:
            return cat
    return "Accessory"


def clean_product_name(raw_name: str, set_name: str) -> str:
    """Extrait le nom du groupe produit depuis le nom WooCommerce brut."""
    raw_name = strip_html(raw_name)
    name = re.sub(r"(?i)star\s*wars[™\s]*:\s*unlimited\s*[-–—]*\s*", "", raw_name).strip()
    if set_name and name.upper().startswith(set_name.upper()):
        name = name[len(set_name):].strip("– -").strip()
    name = name.title() if name.isupper() else name
    return name or raw_name


def derive_clean_variant_name(raw_name: str, product_group: str) -> str:
    variant_name = strip_html(raw_name)
    clean = re.sub(
        r"(?i)star\s*wars[™\s]*:?\s*unlimited\s*[™\s]*", "", variant_name
    ).strip()
    if clean.upper().startswith(product_group.upper()):
        clean = clean[len(product_group):].strip("– -").strip()
    return clean


def compute_attr(attrs: list[dict]) -> tuple[str, str]:
    """Formats possibles : {slug, value}, {attribute, value}, {name, value}."""
    for attr in attrs or []:
        slug = attr.get("slug") or attr.get("attribute") or ""
        name = attr.get("name", "")
        value = attr.get("value", "")
        if not slug and name:
            slug = name.lower().replace(" ", "_")
        if slug and value:
            param = slug if slug.startswith("pa_") else f"pa_{slug}"
            return param, value
    return "", ""


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
    """
    candidates = [
        item for item in catalogue.values()
        if item["setCode"] == set_code and item["productGroup"] == product_group
    ]
    if not candidates:
        return None

    target_raw = clean_name or attr_value

    if _alphanum(target_raw) == _alphanum(product_group) and len(candidates) > 1:
        return None

    target_an = _alphanum(target_raw)
    target_words = _word_set(target_raw)
    target_sorted = sorted(target_an)

    for candidate in candidates:
        cand_name = candidate.get("variantName", "")
        cand_an = _alphanum(cand_name)
        cand_words = _word_set(cand_name)

        if target_an == cand_an:
            return candidate["id"]

        if target_an and cand_an:
            if target_an in cand_an or cand_an in target_an:
                return candidate["id"]

        if target_sorted == sorted(cand_an):
            return candidate["id"]

        if target_words and cand_words:
            shorter, longer = (
                (target_words, cand_words)
                if len(target_words) <= len(cand_words)
                else (cand_words, target_words)
            )
            if shorter and shorter.issubset(longer):
                return candidate["id"]

        if target_an and cand_an and _levenshtein(target_an, cand_an) <= 2:
            return candidate["id"]

    if len(candidates) == 1:
        return candidates[0]["id"]

    return None


# ── Synchronisation ────────────────────────────────────────────────────────────

def backfill_direct_url(item: dict, direct_url: str) -> bool:
    current = item.get("directUrl")
    if current and current != item.get("productPageUrl", ""):
        return False
    if direct_url == current:
        return False
    item["directUrl"] = direct_url
    return direct_url != item.get("productPageUrl", direct_url)


def sync(
    api_products: list[dict],
    products_meta: list[dict],
    existing_catalogue_ids: dict[str, dict],
    images_dir: Path,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Retourne (nouveaux produits, nouvelles variantes, directUrl backfillés)."""
    known_slugs = {p["slug"] for p in products_meta}
    meta_by_slug = {p["slug"]: p for p in products_meta}
    new_products = 0
    new_items = 0
    backfilled = 0

    for product in api_products:
        slug = product.get("slug", "")
        product_url = product.get("permalink") or f"https://www.gamegenic.com/product/{slug}/"

        if slug in known_slugs:
            meta = meta_by_slug[slug]
            set_code = meta["setCode"]
            product_group = meta["productGroup"]
        else:
            set_info = detect_set_category(product.get("categories", []))
            if not set_info:
                print(f"  [SKIP] pas de catégorie de set détectée : {slug}", file=sys.stderr)
                continue
            category_slug, set_name = set_info
            set_code = resolve_set_code(category_slug)
            product_group = clean_product_name(product.get("name", slug), set_name)
            meta = {
                "setCode": set_code,
                "setName": set_name,
                "slug": slug,
                "productGroup": product_group,
                "category": detect_category_from_slug(slug),
            }
            products_meta.append(meta)
            meta_by_slug[slug] = meta
            known_slugs.add(slug)
            new_products += 1
            print(f"→ nouveau produit : {slug} ({set_code} / {product_group})")

        set_name = meta["setName"]
        item_category = meta["category"]
        variations = product.get("variations", [])

        # Produit simple (pas de variantes) : tout est déjà dans la réponse.
        if not variations:
            raw_name = product.get("name", "")
            images = product.get("images", [])
            image_url = images[0].get("src", "") if images else ""
            clean_name = derive_clean_variant_name(raw_name, product_group)
            var_slug = slugify_variant(clean_name) if clean_name else slugify_variant(str(product.get("id")))
            if not clean_name:
                clean_name = product_group
            item_id = make_item_id(set_code, product_group, var_slug)

            if item_id in existing_catalogue_ids:
                if backfill_direct_url(existing_catalogue_ids[item_id], product_url):
                    backfilled += 1
                continue

            new_items += 1
            img_filename = f"{set_code}_{slug.replace('star-wars-unlimited-', '')}_{var_slug}.jpg"
            img_dest = images_dir / img_filename
            img_url_github = BASE_IMG_URL + img_filename
            if image_url and not dry_run and download_image(image_url, img_dest):
                print(f"   ↓ image : {img_filename}")
            elif image_url:
                img_url_github = image_url

            entry = {
                "id": item_id, "setCode": set_code, "setName": set_name,
                "productGroup": product_group, "category": item_category,
                "variantName": clean_name, "variantSlug": var_slug,
                "imageURL": img_url_github, "productPageUrl": product_url,
                "directUrl": product_url, "acquired": False,
                "setOrder": extract_release_month(image_url),
            }
            existing_catalogue_ids[item_id] = entry
            print(f"   + ajouté : {item_id} ({clean_name})")
            continue

        # Produit à variantes.
        for var_item in variations:
            var_id = var_item.get("id") if isinstance(var_item, dict) else var_item
            pre_attrs = var_item.get("attributes", []) if isinstance(var_item, dict) else []
            attr_param, attr_value = compute_attr(pre_attrs)

            if attr_value:
                var_slug = slugify_variant(attr_value)
                item_id = make_item_id(set_code, product_group, var_slug)
                direct_url = f"{product_url}?attribute_{attr_param}={attr_value}"

                if item_id in existing_catalogue_ids:
                    if backfill_direct_url(existing_catalogue_ids[item_id], direct_url):
                        backfilled += 1
                    continue

                fuzzy = _find_fuzzy_match(
                    set_code, product_group, attr_value.replace("-", " ").title(),
                    attr_value, existing_catalogue_ids,
                )
                if fuzzy:
                    if backfill_direct_url(existing_catalogue_ids[fuzzy], direct_url):
                        backfilled += 1
                    continue

            # Pas trouvé (ou pas d'attribut pré-extrait) : c'est potentiellement
            # nouveau, il faut les données complètes (nom + image).
            time.sleep(DELAY)
            var_data = fetch_with_fallback(f"{STORE_API}/{var_id}")
            if not var_data or not isinstance(var_data, dict):
                print(f"  [SKIP] variante {var_id} introuvable", file=sys.stderr)
                continue

            raw_name = var_data.get("name", "")
            images = var_data.get("images", [])
            image_url = images[0].get("src", "") if images else ""
            attr_param2, attr_value2 = compute_attr(var_data.get("attributes", []) or pre_attrs)
            if attr_value2:
                attr_param, attr_value = attr_param2, attr_value2

            clean_name = derive_clean_variant_name(raw_name, product_group)
            if attr_value:
                var_slug = slugify_variant(attr_value)
            elif clean_name:
                var_slug = slugify_variant(clean_name)
            else:
                var_slug = slugify_variant(str(var_id))
            if not clean_name:
                clean_name = attr_value.replace("-", " ").title() if attr_value else product_group

            item_id = make_item_id(set_code, product_group, var_slug)
            direct_url = (
                f"{product_url}?attribute_{attr_param}={attr_value}"
                if attr_param and attr_value else product_url
            )

            if item_id in existing_catalogue_ids:
                if backfill_direct_url(existing_catalogue_ids[item_id], direct_url):
                    backfilled += 1
                continue

            fuzzy = _find_fuzzy_match(set_code, product_group, clean_name, attr_value, existing_catalogue_ids)
            if fuzzy:
                if backfill_direct_url(existing_catalogue_ids[fuzzy], direct_url):
                    backfilled += 1
                continue

            existing_for_group = [
                i for i in existing_catalogue_ids.values()
                if i["setCode"] == set_code and i["productGroup"] == product_group
            ]
            if _alphanum(clean_name) == _alphanum(product_group) and existing_for_group:
                print(f"   ~ ignoré (variant générique ambigu) : {item_id}")
                continue

            new_items += 1
            img_filename = f"{set_code}_{slug.replace('star-wars-unlimited-', '')}_{var_slug}.jpg"
            img_dest = images_dir / img_filename
            img_url_github = BASE_IMG_URL + img_filename
            if image_url and not dry_run and download_image(image_url, img_dest):
                print(f"   ↓ image : {img_filename}")
            elif image_url:
                img_url_github = image_url

            entry = {
                "id": item_id, "setCode": set_code, "setName": set_name,
                "productGroup": product_group, "category": item_category,
                "variantName": clean_name, "variantSlug": var_slug,
                "imageURL": img_url_github, "productPageUrl": product_url,
                "directUrl": direct_url, "acquired": False,
                "setOrder": extract_release_month(image_url),
            }
            existing_catalogue_ids[item_id] = entry
            print(f"   + ajouté : {item_id} ({clean_name})")

    return new_products, new_items, backfilled


# ── Point d'entrée ────────────────────────────────────────────────────────────

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

    products_meta: list[dict] = json.loads(products_path.read_text())
    catalogue: list[dict] = json.loads(catalogue_path.read_text()) if catalogue_path.exists() else []
    existing_catalogue_ids: dict[str, dict] = {item["id"]: item for item in catalogue}

    api_products = fetch_all_swu_products()

    images_dir.mkdir(parents=True, exist_ok=True)
    new_products, new_items, backfilled = sync(
        api_products, products_meta, existing_catalogue_ids, images_dir, args.dry_run
    )

    print(
        f"\n{new_products} nouveau(x) produit(s), {new_items} nouvelle(s) variante(s), "
        f"{backfilled} directUrl backfillé(s)."
    )

    if args.dry_run:
        print("[dry-run] Aucun fichier modifié.")
        return

    if new_products:
        products_path.write_text(json.dumps(products_meta, indent=2, ensure_ascii=False) + "\n")
    if new_items or backfilled:
        catalogue_path.write_text(
            json.dumps(list(existing_catalogue_ids.values()), indent=2, ensure_ascii=False) + "\n"
        )
        print(f"catalogue.json mis à jour ({len(existing_catalogue_ids)} items total).")
    if not (new_products or new_items or backfilled):
        print("Rien à mettre à jour.")


if __name__ == "__main__":
    main()
