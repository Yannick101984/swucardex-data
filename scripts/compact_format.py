#!/usr/bin/env python3
"""
Formats compacts lus par les apps (SWUCardex#43, SWUCardex#834).

- Format v3 des sets  : sets/v3/{id}.json
  {"formatVersion": 3, "set": "SOR", "cards": [ {...}, ... ]}
  Une carte par ligne, champs vides omis, URLs d'images relatives au CDN.

- Packs langue        : sets/lang/{locale}/{id}.json
  {"formatVersion": 1, "locale": "de", "set": "SOR", "cards": {"<cardUid>": {...}, ...}}
  Clé = cardUid, identique dans toutes les langues : aucun lien obligatoire avec la VF.

Voir README, section « Formats compacts ».
"""

import json
import os

CDN_PREFIX = "https://cdn.starwarsunlimited.com/"
V3_FORMAT_VERSION = 3
LANG_PACK_FORMAT_VERSION = 1
LANG_PACK_LOCALES = ("fr", "en", "de", "es", "it")

_HERE    = os.path.dirname(os.path.abspath(__file__))
SETS_DIR = os.path.join(_HERE, '..', 'sets')


def v3_path(set_id):
    return os.path.join(SETS_DIR, 'v3', f"{set_id.lower()}.json")


def lang_pack_path(locale, set_id):
    return os.path.join(SETS_DIR, 'lang', locale, f"{set_id.lower()}.json")


# ─── Lecture des enveloppes Strapi ────────────────────────────────────────────

def _data(obj):
    return (obj or {}).get('data') if isinstance(obj, dict) else None


def _attributes(obj):
    """Attributs d'une relation simple ({data: {attributes: {...}}})."""
    item = _data(obj)
    return (item or {}).get('attributes') or {} if isinstance(item, dict) else {}


def _names(obj, key='name'):
    """Liste des `key` d'une relation multiple, None si vide."""
    items = _data(obj)
    if not isinstance(items, list):
        return None
    values = [(i.get('attributes') or {}).get(key) for i in items]
    values = [v for v in values if v is not None]
    return values or None


def relative_url(url):
    """URL relative au CDN (préfixe ajouté côté app), inchangée si hors CDN."""
    if not url:
        return None
    return url[len(CDN_PREFIX):] if url.startswith(CDN_PREFIX) else url


def _art_urls(attrs):
    front   = _attributes(attrs.get('artFront'))
    formats = front.get('formats') or {}
    thumb   = formats.get('xxsmall') or formats.get('xxxsmall') or {}
    return {
        'FrontArt':     relative_url(front.get('url')),
        'cardArt':      relative_url((formats.get('card') or {}).get('url')),
        'thumbnailArt': relative_url(thumb.get('url')),
        'BackArt':      relative_url(_attributes(attrs.get('artBack')).get('url')),
    }


def _english_name(attrs):
    """Nom anglais « Titre, Sous-titre » (lookup prix Cardmarket), comme Card.from côté iOS."""
    for loc in _data(attrs.get('localizations')) or []:
        la = loc.get('attributes') or {}
        if la.get('locale') != 'en':
            continue
        title    = (la.get('title') or '').strip()
        subtitle = (la.get('subtitle') or '').strip()
        if not title:
            return None
        return f"{title}, {subtitle}" if subtitle else title
    return None


def _compact(fields):
    return {k: v for k, v in fields.items() if v is not None and v != [] and v != ''}


# ─── Conversion ───────────────────────────────────────────────────────────────

def card_to_v3(card):
    """
    Carte brute de l'API → carte v3. Mêmes règles que Card.from (iOS) et
    StrapiCard.toDomain (Android) : les Card.id doivent rester identiques.
    """
    attrs      = card['attributes']
    card_type  = _attributes(attrs.get('type'))
    variants   = _names(attrs.get('variantTypes'))
    variant_of = _data(attrs.get('variantOf'))
    reprint_of = _data(attrs.get('reprintOf'))
    return _compact({
        'jsonId':       card['id'],
        'cardUid':      attrs.get('cardUid'),
        'Number':       attrs.get('cardNumber'),
        'Name':         attrs.get('title'),
        'Subtitle':     attrs.get('subtitle'),
        'type':         card_type.get('value') or card_type.get('name'),
        'Rarity':       _attributes(attrs.get('rarity')).get('name'),
        'VariantType':  variants[0] if variants else None,
        'Aspects':      _names(attrs.get('aspects')),
        'AspectsEn':    _names(attrs.get('aspects'), 'englishName'),
        'Traits':       _names(attrs.get('traits')),
        'Arenas':       _names(attrs.get('arenas')),
        'Keywords':     _names(attrs.get('keywords')),
        'Cost':         attrs.get('cost'),
        'Power':        attrs.get('power'),
        'HP':           attrs.get('hp'),
        'FrontText':    attrs.get('text'),
        'EpicAction':   attrs.get('epicAction'),
        'DeployBox':    attrs.get('deployBox'),
        'Rules':        attrs.get('rules'),
        'Unique':       attrs.get('unique'),
        'Artist':       attrs.get('artist'),
        'cardCount':    attrs.get('cardCount'),
        'UpgradeHP':    attrs.get('upgradeHp'),
        'UpgradePower': attrs.get('upgradePower'),
        'parentCardId': variant_of.get('id') if isinstance(variant_of, dict) else None,
        'reprintOfId':  reprint_of.get('id') if isinstance(reprint_of, dict) else None,
        'validationId': attrs.get('validationId'),
        'enName':       _english_name(attrs),
        **_art_urls(attrs),
    })


def card_to_lang_entry(card):
    """Carte brute d'une locale → entrée de pack langue (textes affichés + images)."""
    attrs = card['attributes']
    return _compact({
        'Name':       attrs.get('title'),
        'Subtitle':   attrs.get('subtitle'),
        'typeName':   _attributes(attrs.get('type')).get('name'),
        'Rarity':     _attributes(attrs.get('rarity')).get('name'),
        'Aspects':    _names(attrs.get('aspects')),
        'Traits':     _names(attrs.get('traits')),
        'Arenas':     _names(attrs.get('arenas')),
        'Keywords':   _names(attrs.get('keywords')),
        'FrontText':  attrs.get('text'),
        'EpicAction': attrs.get('epicAction'),
        'DeployBox':  attrs.get('deployBox'),
        'Rules':      attrs.get('rules'),
        **_art_urls(attrs),
    })


# ─── Écriture ─────────────────────────────────────────────────────────────────

def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def render_v3(set_code, raw_cards):
    """Texte du fichier v3 : une carte par ligne (diffs git lisibles)."""
    lines = ",\n".join(_dumps(card_to_v3(c)) for c in raw_cards)
    head  = f'{{"formatVersion":{V3_FORMAT_VERSION},"set":{_dumps(set_code.upper())},"cards":[\n'
    return head + lines + "\n]}\n"


def render_lang_pack(locale, set_code, entries):
    """Texte d'un pack langue : une carte par ligne, ordre stable (entries déjà trié)."""
    lines = ",\n".join(f"{_dumps(str(uid))}:{_dumps(entry)}" for uid, entry in entries)
    head  = (f'{{"formatVersion":{LANG_PACK_FORMAT_VERSION},"locale":{_dumps(locale)},'
             f'"set":{_dumps(set_code.upper())},"cards":{{\n')
    return head + lines + "\n}}\n"


def write_if_changed(path, text):
    """Écrit le fichier s'il diffère du contenu actuel. Retourne True si écrit."""
    try:
        with open(path, encoding='utf-8') as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return True


def write_v3(set_code, raw_cards):
    return write_if_changed(v3_path(set_code), render_v3(set_code, raw_cards))
