#!/usr/bin/env python3
"""
Packs langue des sets (SWUCardex#834).

Pour chaque set du manifest et chaque langue (fr, en, de, es, it), récupère les
cartes du set dans cette langue et écrit sets/lang/{locale}/{id}.json : textes
affichés et images de chaque carte, indexés par cardUid (identique dans toutes
les langues, aucun lien obligatoire avec la VF).

Manifest, par set :
  langPacksURL    : URL avec {locale} (posée par sync_sets.py)
  langPackLocales : langues ayant un pack non vide
  langPackVersion : augmenté quand au moins un pack du set change

Une carte absente d'une langue est simplement absente de son pack. Un set dont
une langue n'a pas pu être récupérée en entier garde ses packs actuels.

Économie de requêtes sur l'API officielle (éviter un blocage) :
- pages de 250 cartes (maximum accepté par l'API), 3 s de pause entre deux pages ;
- pack fr construit depuis le JSON brut local, déjà scrapé en français : aucune requête ;
- --reuse DIR : reprend les packs déjà générés dans un autre checkout (DIR/sets/lang)
  au lieu de scraper. Le workflow scrape une fois pour main, puis réutilise pour staging.

Usage : python3 scripts/sync_lang_packs.py [--sets sor,shd] [--locales de,it] [--reuse DIR]
"""

import argparse
import json
import os
import sys
import time

from compact_format import (
    LANG_PACK_LOCALES, card_to_lang_entry, lang_pack_path, render_lang_pack, write_if_changed,
)
from sync_sets import (
    CURRENT_BRANCH, api_get, ensure_compact_urls, load_manifest, normalize_data_urls, save_manifest,
    set_file_path,
)

PAGE_SIZE   = 250   # maximum accepté par l'API (au-delà, elle répond quand même 250)
PAGE_PAUSE  = 3     # secondes entre deux requêtes : ménager l'API officielle
SET_PAUSE   = 3
PAGE_TIMEOUT = 120  # une page de 250 cartes met 7 à 15 s, parfois bien plus
LOCAL_LOCALE = "fr" # langue des JSON bruts de sets/ : pack construit sans requête


def fetch_locale_cards(api_code, locale):
    """Toutes les cartes d'un set dans une langue, None si la récupération est incomplète."""
    cards, page, expected = [], 1, None
    while True:
        data = api_get("card-list", {
            'locale':                                  locale,
            'filters[$and][0][expansion][code][$eq]': api_code.upper(),
            'pagination[pageSize]':                   str(PAGE_SIZE),
            'pagination[page]':                       str(page),
            # id en second critère : sans lui, l'ordre des cartes de même numéro varie
            # d'une page à l'autre et une carte peut être lue deux fois, une autre jamais
            'sort[0]':                                'cardNumber:asc',
            'sort[1]':                                'id:asc',
        }, timeout=PAGE_TIMEOUT)
        if not data or 'data' not in data:
            print(f"    ❌ {locale} : page {page} illisible")
            return None
        cards.extend(data['data'])
        meta = data.get('meta', {}).get('pagination', {})
        if expected is None:
            expected = meta.get('total', 0)
        if not data['data'] or meta.get('page', page) >= meta.get('pageCount', 0):
            break
        page += 1
        if page > 100:
            print(f"    ⚠️  {locale} : plus de 100 pages, arrêt")
            return None
        time.sleep(PAGE_PAUSE)
    unique = len({c.get('id') for c in cards})
    if expected and unique < expected:
        print(f"    ⚠️  {locale} : {unique}/{expected} cartes distinctes, scrape incomplet")
        return None
    return cards


def build_entries(cards):
    """[(cardUid, entrée)] triés par numéro puis cardUid ; cartes sans cardUid ignorées."""
    by_uid = {}
    for card in cards:
        attrs = card.get('attributes') or {}
        uid = attrs.get('cardUid')
        if uid:
            by_uid[str(uid)] = (attrs.get('cardNumber') or 0, card_to_lang_entry(card))
    return [(uid, entry) for uid, (_, entry) in sorted(by_uid.items(), key=lambda kv: (kv[1][0], kv[0]))]


def local_cards(code):
    """Cartes du JSON brut local (français), None s'il est absent."""
    try:
        with open(set_file_path(code), encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    return raw.get('data', []) + raw.get('variants', [])


def reused_text(reuse_dir, locale, code):
    """Pack déjà généré dans un autre checkout, '' s'il n'y existe pas."""
    path = os.path.join(reuse_dir, 'sets', 'lang', locale, f"{code.lower()}.json")
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return ''


def sync_set(entry, locales, reuse_dir=None):
    """Met à jour les packs d'un set. Retourne True si le manifest a changé."""
    code     = entry['code'].upper()
    api_code = entry.get('displayCode', code).upper()
    print(f"\n📦 {code}")

    rendered = {}
    for locale in locales:
        if reuse_dir:
            rendered[locale] = reused_text(reuse_dir, locale, code) or None
            continue
        if locale == LOCAL_LOCALE:
            cards = local_cards(code)
        else:
            cards = fetch_locale_cards(api_code, locale)
            time.sleep(PAGE_PAUSE)
        if cards is None:
            print(f"  ⏭️  {code} : packs inchangés (langue {locale} incomplète)")
            return False
        entries = build_entries(cards)
        print(f"    {locale} : {len(entries)} cartes")
        rendered[locale] = render_lang_pack(locale, code, entries) if entries else None

    changed = False
    for locale, text in rendered.items():
        if text is not None and write_if_changed(lang_pack_path(locale, code), text):
            print(f"  💾 sets/lang/{locale}/{code.lower()}.json")
            changed = True

    manifest_changed = ensure_compact_urls(entry)
    available = sorted(
        (loc for loc in LANG_PACK_LOCALES
         if rendered.get(loc) is not None or (loc not in rendered and _pack_exists(loc, code))),
        key=LANG_PACK_LOCALES.index,
    )
    if entry.get('langPackLocales') != available:
        entry['langPackLocales'] = available
        manifest_changed = True
    if changed or 'langPackVersion' not in entry:
        entry['langPackVersion'] = entry.get('langPackVersion', 0) + 1
        manifest_changed = True
    return manifest_changed


def _pack_exists(locale, code):
    try:
        with open(lang_pack_path(locale, code), encoding='utf-8'):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Packs langue des sets SWU")
    parser.add_argument('--sets', help="ids de sets séparés par des virgules (défaut : tous)")
    parser.add_argument('--locales', help="langues séparées par des virgules (défaut : fr,en,de,es,it)")
    parser.add_argument('--reuse', metavar='DIR', help="reprendre les packs de DIR/sets/lang sans scraper")
    args = parser.parse_args()

    locales = args.locales.split(',') if args.locales else list(LANG_PACK_LOCALES)
    unknown = [loc for loc in locales if loc not in LANG_PACK_LOCALES]
    if unknown:
        sys.exit(f"Langues inconnues : {unknown}")
    wanted = {s.strip().lower() for s in args.sets.split(',')} if args.sets else None

    print(f"\n🌍 PACKS LANGUE : branche {CURRENT_BRANCH}, langues {locales}")
    print("=" * 60)
    manifest = load_manifest()
    manifest_changed = False
    for entry in manifest['sets']:
        if wanted and entry['id'] not in wanted:
            continue
        manifest_changed |= sync_set(entry, locales, args.reuse)
        if not args.reuse:
            time.sleep(SET_PAUSE)

    manifest_changed |= normalize_data_urls(manifest) > 0
    if manifest_changed:
        save_manifest(manifest)
        print("\n📝 manifest.json mis à jour")
    print("\n✅ Terminé")


if __name__ == '__main__':
    main()
