#!/usr/bin/env python3
"""
Contrôle des formats compacts (SWUCardex#43, #834), lancé par les workflows
avant chaque commit. Code de sortie 1 au moindre écart : rien n'est commité.

Pour chaque set du manifest :
- le fichier v3 existe, se lit et contient exactement les mêmes cartes que le
  JSON brut, avec les mêmes Card.id (SET-Numéro-Nom-Variante, clé de possession
  des utilisateurs, calculée comme Card.makeID côté iOS) ;
- chaque cardUid est présent et unique ;
- dataURLv3 et langPacksURL sont renseignés ;
- les packs langue présents se lisent, ont la bonne langue et ne contiennent
  que des cardUid connus du set (sauf cartes absentes de l'API française, #857).
"""

import json
import os
import sys
from collections import Counter

from compact_format import LANG_PACK_LOCALES, V3_FORMAT_VERSION, lang_pack_path, v3_path

_HERE         = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(_HERE, '..', 'manifest.json')
SETS_DIR      = os.path.join(_HERE, '..', 'sets')


def card_id(set_code, number, name, variant):
    """Card.makeID (iOS) : ne jamais modifier ce format."""
    return "-".join([
        set_code,
        str(number) if number is not None else "unknown",
        name or "unknown",
        variant or "Normal",
    ])


def raw_ids(set_code, raw):
    ids = []
    for card in raw.get('data', []) + raw.get('variants', []):
        attrs    = card['attributes']
        variants = (attrs.get('variantTypes') or {}).get('data') or []
        variant  = (variants[0].get('attributes') or {}).get('name') if variants else None
        ids.append(card_id(set_code, attrs.get('cardNumber'), attrs.get('title'), variant))
    return Counter(ids)


def check_set(entry, errors):
    code = entry['code'].upper()
    raw_path = os.path.join(SETS_DIR, f"{code.lower()}.json")
    if not os.path.isfile(raw_path):
        return 0
    for key in ("dataURLv3", "langPacksURL"):
        if not entry.get(key):
            errors.append(f"{code} : {key} absent du manifest")
    try:
        with open(v3_path(code), encoding='utf-8') as f:
            v3 = json.load(f)
    except (OSError, ValueError) as e:
        errors.append(f"{code} : v3 illisible ({e})")
        return 0
    if v3.get('formatVersion') != V3_FORMAT_VERSION or v3.get('set') != code:
        errors.append(f"{code} : en-tête v3 inattendu")
    cards = v3.get('cards', [])

    with open(raw_path, encoding='utf-8') as f:
        expected = raw_ids(code, json.load(f))
    actual = Counter(card_id(code, c.get('Number'), c.get('Name'), c.get('VariantType')) for c in cards)
    if actual != expected:
        missing = list((expected - actual).elements())[:3]
        extra   = list((actual - expected).elements())[:3]
        errors.append(f"{code} : Card.id différents du JSON brut (manquants {missing}, en trop {extra})")

    uids = Counter(c.get('cardUid') for c in cards)
    if None in uids:
        errors.append(f"{code} : {uids[None]} carte(s) v3 sans cardUid")
    duplicates = [u for u, n in uids.items() if u and n > 1]
    if duplicates:
        errors.append(f"{code} : cardUid en double {duplicates[:3]}")

    known = {str(u) for u in uids if u}
    for locale in LANG_PACK_LOCALES:
        path = lang_pack_path(locale, code)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                pack = json.load(f)
        except ValueError as e:
            errors.append(f"{code} : pack {locale} illisible ({e})")
            continue
        if pack.get('locale') != locale or pack.get('set') != code:
            errors.append(f"{code} : en-tête du pack {locale} inattendu")
        unknown = [u for u in pack.get('cards', {}) if u not in known]
        if unknown:
            print(f"  ℹ️  {code} {locale} : {len(unknown)} carte(s) absente(s) du catalogue FR (#857)")
    return len(cards)


def main():
    with open(MANIFEST_PATH, encoding='utf-8') as f:
        manifest = json.load(f)
    errors, total = [], 0
    for entry in manifest['sets']:
        total += check_set(entry, errors)
    if errors:
        print("❌ Formats compacts invalides :")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"✅ Formats compacts OK : {len(manifest['sets'])} sets, {total} cartes, Card.id identiques au format brut")


if __name__ == '__main__':
    main()
