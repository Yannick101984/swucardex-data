#!/usr/bin/env python3
"""
Packs langue des sets (SWUCardex#834).

Pour chaque set du manifest et chaque langue (fr, en, de, es, it), tient à jour
sets/lang/{locale}/{id}.json : textes affichés et images de chaque carte, indexés
par cardUid (identique dans toutes les langues, aucun lien obligatoire avec la VF).

Manifest, par set :
  langPacksURL    : URL avec {locale} (posée par sync_sets.py)
  langPackLocales : langues ayant un pack non vide
  langPackVersion : augmenté quand au moins un pack du set change

Mode incrémental (par défaut) : seules les cartes manquantes sont demandées.
- Pack fr construit depuis le JSON brut local, déjà scrapé en français : aucune requête.
  Il sert de référence : une carte du JSON brut absente du pack d'une langue est
  « manquante » dans cette langue.
- Langue sans pack pour ce set (nouveau set) : tout le set est récupéré dans cette langue.
- Sinon : seules les cartes manquantes sont demandées (filtre cardUid). Un set sans
  carte manquante ne coûte aucune requête.
- Carte toujours introuvable (pas encore publiée dans cette langue) : notée dans
  sets/lang/pending.json avec sa date, et redemandée automatiquement au passage
  suivant (le workflow tourne après chaque comparaison des sets). Abandonnée au bout
  de RETRY_DAYS jours : elle n'existe sans doute pas dans cette langue.
- Les cartes déjà présentes dans un pack ne sont jamais retirées ni redemandées.
  --full re-scrape tout (correction de textes ou d'images d'une langue) et remet
  à zéro les abandons.

Économie de requêtes sur l'API officielle (éviter un blocage) :
- pages de 250 cartes (maximum accepté par l'API), 3 s de pause entre deux pages ;
- cartes manquantes demandées par lots de UID_BATCH ;
- --reuse DIR : reprend les packs déjà générés dans un autre checkout (DIR/sets/lang)
  au lieu de scraper. Le workflow scrape une fois sur staging, puis main réutilise.

Usage : python3 scripts/sync_lang_packs.py [--sets sor,shd] [--locales de,it] [--full | --reuse DIR]
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

from compact_format import (
    LANG_PACK_LOCALES, card_to_lang_entry, lang_pack_path, render_lang_pack, write_if_changed,
)
from sync_sets import (
    CURRENT_BRANCH, api_get, ensure_compact_urls, load_manifest, normalize_data_urls, save_manifest,
    set_file_path,
)

PAGE_SIZE   = 250   # maximum accepté par l'API (au-delà, elle répond quand même 250)
PAGE_PAUSE  = 3     # secondes entre deux requêtes : ménager l'API officielle
PAGE_TIMEOUT = 120  # une page de 250 cartes met 7 à 15 s, parfois bien plus
LOCAL_LOCALE = "fr" # langue des JSON bruts de sets/ : pack construit sans requête
UID_BATCH   = 100   # cardUid par requête en mode incrémental (longueur de l'URL)
RETRY_DAYS  = 60    # au-delà, une carte manquante n'est plus redemandée
PENDING_PATH = os.path.join(os.path.dirname(os.path.dirname(lang_pack_path(LOCAL_LOCALE, 'x'))), 'pending.json')


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


def fetch_cards_by_uid(locale, uids):
    """Cartes d'une langue parmi `uids`, None si une requête échoue."""
    cards = []
    for start in range(0, len(uids), UID_BATCH):
        params = {'locale': locale, 'pagination[pageSize]': str(PAGE_SIZE)}
        for i, uid in enumerate(uids[start:start + UID_BATCH]):
            params[f'filters[$and][0][cardUid][$in][{i}]'] = uid
        data = api_get("card-list", params, timeout=PAGE_TIMEOUT)
        if not data or 'data' not in data:
            print(f"    ❌ {locale} : requête par cardUid illisible")
            return None
        cards.extend(data['data'])
        time.sleep(PAGE_PAUSE)
    return cards


def read_pack(locale, code):
    """Cartes du pack actuel {cardUid: entrée}, None s'il n'existe pas."""
    try:
        with open(lang_pack_path(locale, code), encoding='utf-8') as f:
            return json.load(f).get('cards', {})
    except (OSError, ValueError):
        return None


def load_pending():
    try:
        with open(PENDING_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_pending(pending):
    text = json.dumps(pending, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if write_if_changed(PENDING_PATH, text):
        print("\n📝 sets/lang/pending.json mis à jour")


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


def sync_set(entry, locales, pending, reuse_dir=None, full=False):
    """Met à jour les packs d'un set. Retourne True si le manifest a changé."""
    code     = entry['code'].upper()
    api_code = entry.get('displayCode', code).upper()
    print(f"\n📦 {code}")

    rendered = {}
    if reuse_dir:
        for locale in locales:
            rendered[locale] = reused_text(reuse_dir, locale, code) or None
    else:
        fr_cards = local_cards(code)
        if fr_cards is None:
            print(f"  ⏭️  {code} : JSON brut absent, packs inchangés")
            return False
        reference = build_entries(fr_cards)
        set_pending = pending.setdefault(entry['id'], {})
        for locale in locales:
            if locale == LOCAL_LOCALE:
                rendered[locale] = render_lang_pack(locale, code, reference) if reference else None
                print(f"    {locale} : {len(reference)} cartes (JSON brut)")
                continue
            entries = sync_locale(code, api_code, locale, reference, set_pending, full)
            rendered[locale] = render_lang_pack(locale, code, entries) if entries else None
        for locale in [loc for loc, uids in set_pending.items() if not uids]:
            del set_pending[locale]
        if not set_pending:
            del pending[entry['id']]

    changed = False
    for locale, text in rendered.items():
        if text is not None and write_if_changed(lang_pack_path(locale, code), text):
            print(f"  💾 sets/lang/{locale}/{code.lower()}.json")
            changed = True

    manifest_changed = ensure_compact_urls(entry)
    # rendered[locale] None = pack laissé tel quel : la liste suit les fichiers présents
    available = [loc for loc in LANG_PACK_LOCALES if _pack_exists(loc, code)]
    if entry.get('langPackLocales') != available:
        entry['langPackLocales'] = available
        manifest_changed = True
    if changed or 'langPackVersion' not in entry:
        entry['langPackVersion'] = entry.get('langPackVersion', 0) + 1
        manifest_changed = True
    return manifest_changed


def sync_locale(code, api_code, locale, reference, set_pending, full):
    """
    Entrées du pack d'une langue après mise à jour, None pour le laisser tel quel.
    Tient à jour set_pending[locale] = {cardUid: date de la première absence}.
    """
    today = date.today()
    current = None if full else read_pack(locale, code)
    waiting = {} if full else set_pending.get(locale, {})
    missing = [uid for uid, _ in reference if current is None or uid not in current]
    abandoned = [uid for uid in missing if uid in waiting
                 and today - date.fromisoformat(waiting[uid]) > timedelta(days=RETRY_DAYS)]
    wanted = [uid for uid in missing if uid not in abandoned]

    if not wanted:
        detail = f", {len(abandoned)} abandonnée(s)" if abandoned else ""
        print(f"    {locale} : rien à demander{detail}, aucune requête")
        set_pending[locale] = {uid: waiting[uid] for uid in abandoned}
        return None

    if current is None:
        cards = fetch_locale_cards(api_code, locale)
        time.sleep(PAGE_PAUSE)
    else:
        cards = fetch_cards_by_uid(locale, wanted)
    if cards is None:
        print(f"    ⏭️  {locale} : échec, nouvelle tentative au prochain passage")
        set_pending[locale] = {uid: waiting.get(uid, today.isoformat()) for uid in missing}
        return None

    fetched = build_entries(cards)
    merged = dict(current or {})
    merged.update(fetched)
    still = [uid for uid, _ in reference if uid not in merged]
    set_pending[locale] = {uid: waiting.get(uid, today.isoformat()) for uid in still}
    found = len([uid for uid, _ in fetched if current is None or uid not in current])
    print(f"    {locale} : {found} carte(s) récupérée(s), {len(still)} toujours manquante(s)"
          + (f" dont {len(abandoned)} abandonnée(s)" if abandoned else ""))
    if current is None:
        return fetched
    # cartes déjà présentes dans leur ordre, nouvelles ajoutées à la suite
    return list(current.items()) + [(uid, e) for uid, e in fetched if uid not in current]


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
    parser.add_argument('--full', action='store_true',
                        help="re-scraper toutes les cartes au lieu des seules manquantes")
    args = parser.parse_args()
    if args.full and args.reuse:
        sys.exit("--full et --reuse sont incompatibles")

    locales = args.locales.split(',') if args.locales else list(LANG_PACK_LOCALES)
    unknown = [loc for loc in locales if loc not in LANG_PACK_LOCALES]
    if unknown:
        sys.exit(f"Langues inconnues : {unknown}")
    wanted = {s.strip().lower() for s in args.sets.split(',')} if args.sets else None

    print(f"\n🌍 PACKS LANGUE : branche {CURRENT_BRANCH}, langues {locales}")
    print("=" * 60)
    manifest = load_manifest()
    manifest_changed = False
    pending = load_pending()
    for entry in manifest['sets']:
        if wanted and entry['id'] not in wanted:
            continue
        manifest_changed |= sync_set(entry, locales, pending, args.reuse, args.full)

    if not args.reuse:
        save_pending(pending)
    manifest_changed |= normalize_data_urls(manifest) > 0
    if manifest_changed:
        save_manifest(manifest)
        print("\n📝 manifest.json mis à jour")
    print("\n✅ Terminé")


if __name__ == '__main__':
    main()
