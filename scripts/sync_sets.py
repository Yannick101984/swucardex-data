#!/usr/bin/env python3
"""
Synchronisation automatique des sets Star Wars Unlimited.

Modes :
  sync    — Détecte les nouveaux sets, scrape les cartes, ajoute les JSONs,
             met à jour manifest.json (entrée de set, latestMainSet).
             Écrit une annonce dans announcements.json.
  compare — Compare les sets existants (hors SOR/TWI/SHD) avec l'API,
             met à jour les JSONs si des différences sont trouvées,
             bumpe dataVersion dans le manifest et écrit une annonce détaillée
             (liste des cartes ajoutées / règles modifiées) dans announcements.json.
"""

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request
import requests
from collections import Counter
from datetime import date

# ─── Paths ────────────────────────────────────────────────────────────────────
_HERE              = os.path.dirname(os.path.abspath(__file__))
SETS_DIR           = os.path.join(_HERE, '..', 'sets')
MANIFEST_PATH      = os.path.join(_HERE, '..', 'manifest.json')
ANNOUNCEMENTS_PATH = os.path.join(_HERE, '..', 'announcements.json')

# ─── Config ───────────────────────────────────────────────────────────────────
REPO   = "Yannick101984/swucardex-data"
BRANCH = os.environ.get("GITHUB_REF_NAME", "staging")

COMPARE_EXCLUDED = {'SOR', 'TWI', 'SHD'}

IGNORED_FIELDS = {
    'textStyled', 'deployBoxStyled', 'epicActionStyled',
    'rulesStyled', 'updatedAt', 'createdAt', 'linkHtml',
}

API_BASE   = "https://admin.starwarsunlimited.com/api"
SITE_BASE  = "https://starwarsunlimited.com"
HEADERS    = {
    'User-Agent':      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept':          'application/json',
    'Accept-Language': 'fr-FR,fr;q=0.9',
    'Referer':         'https://starwarsunlimited.com/',
    'Origin':          'https://starwarsunlimited.com',
}
HEADERS_HTML = {**HEADERS, 'Accept': 'text/html,application/xhtml+xml'}


# ─── Helpers réseau ───────────────────────────────────────────────────────────

def api_get(path, params=None, retries=3):
    url = f"{API_BASE}/{path}"
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 60))
                print(f"⏳ Rate limit — attente {wait}s...")
                time.sleep(wait)
                continue
            print(f"⚠️  HTTP {r.status_code} pour {url}")
            return None
        except Exception as e:
            print(f"❌ Erreur réseau (tentative {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(5)
    return None


def http_get_bytes(url, retries=3):
    """
    Télécharge un fichier binaire depuis le CDN via urllib.request.
    requests normalise les doubles slashs (//), ce qui cause un 403 sur le CDN S3.
    urllib.request préserve l'URL telle quelle.
    """
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            print(f"❌ Erreur téléchargement (tentative {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(3)
    return None


# ─── Helpers cartes ───────────────────────────────────────────────────────────

def fetch_cards(api_code):
    print(f"  🃏 Scraping {api_code}...")
    all_cards = []
    page = 1
    expected_total = None
    while True:
        params = {
            'locale':                                   'fr',
            'filters[$and][0][expansion][code][$eq]':  api_code.upper(),
            'pagination[pageSize]':                    '120',
            'pagination[page]':                        str(page),
            'sort[0]':                                 'cardNumber:asc',
        }
        data = api_get("card-list", params)
        if not data or 'data' not in data:
            print(f"  ❌ Impossible de récupérer la page {page}")
            break
        cards = data['data']
        if not cards:
            break
        all_cards.extend(cards)
        meta    = data.get('meta', {}).get('pagination', {})
        current = meta.get('page', page)
        last    = meta.get('pageCount', 0)
        total   = meta.get('total', 0)
        if expected_total is None:
            expected_total = total
        print(f"  Page {current}/{last} — {len(cards)} cartes (total API: {total})")
        if last and current >= last:
            break
        page += 1
        time.sleep(2)
        if page > 100:
            print("  ⚠️  Sécurité : >100 pages, arrêt")
            break
    if expected_total and len(all_cards) < expected_total:
        print(f"  ⚠️  Scrape incomplet : {len(all_cards)}/{expected_total} — {api_code} ignoré")
        return None
    print(f"  ✅ {len(all_cards)} cartes récupérées pour {api_code}")
    return all_cards


def fetch_all_api_expansions():
    """
    Retourne {api_code (upper): {'name': str, 'sortValue': int}}
    via la requête cardNumber<=10 (couvre tous les sets, même ceux qui commencent à >1).
    """
    found = {}
    page  = 1
    while True:
        params = {
            'locale':                               'fr',
            'pagination[pageSize]':                '250',
            'pagination[page]':                    str(page),
            'filters[$and][0][cardNumber][$lte]':  '10',
            'fields[0]':                           'cardNumber',
            'populate[expansion][fields][0]':      'code',
            'populate[expansion][fields][1]':      'name',
            'populate[expansion][fields][2]':      'sortValue',
        }
        data = api_get("card-list", params)
        if not data or 'data' not in data:
            break
        for card in data['data']:
            exp   = card.get('attributes', {}).get('expansion', {}).get('data', {})
            attrs = exp.get('attributes', {}) if isinstance(exp, dict) else {}
            code  = (attrs.get('code') or '').upper()
            if code and code not in found:
                found[code] = {
                    'name':      attrs.get('name', code),
                    'sortValue': attrs.get('sortValue', 999),
                }
        pagination = data.get('meta', {}).get('pagination', {})
        if pagination.get('page', 1) >= pagination.get('pageCount', 1):
            break
        page += 1
        time.sleep(1)
        if page > 20:
            break
    return found


# ─── Media kit ────────────────────────────────────────────────────────────────

def fetch_mediakit_main_chapters():
    """
    Récupère la nav du media kit et retourne un dict
    {title_normalisé: chapter_id} pour tous les chapitres sous "Assets de set".
    La présence d'un chapitre dans "Assets de set" indique un set de type "main".
    """
    try:
        r = requests.get(
            f"{SITE_BASE}/fr/media-kit",
            headers=HEADERS_HTML,
            timeout=20,
        )
        html = r.text
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html, re.DOTALL,
        )
        if not match:
            print("  ⚠️  __NEXT_DATA__ introuvable dans le media kit")
            return {}

        next_data = json.loads(match.group(1))
        nav = next_data.get('props', {}).get('pageProps', {}).get('nav', [])

        chapters = {}
        for section in nav:
            # "Assets de set" uniquement — "assets de marque" contient aussi "set"
            # donc on vérifie la présence de "de set" comme mot complet
            if 'de set' in section.get('title', '').lower():
                for ch in section.get('chapters', []):
                    normalized = ch['title'].lower().strip()
                    chapters[normalized] = ch['id']
        print(f"  📋 {len(chapters)} sets dans le media kit : {list(chapters.keys())}")
        return chapters

    except Exception as e:
        print(f"  ⚠️  Erreur lecture media kit nav : {e}")
        return {}


def _extract_color_from_swatch(swatch_url):
    """
    Télécharge l'image swatch (xsmall ~300px) et retourne le code hex
    de la couleur dominante dans le quart gauche de l'image.
    Requiert Pillow.
    """
    try:
        from PIL import Image
        data = http_get_bytes(swatch_url)
        if not data:
            return None
        img = Image.open(io.BytesIO(data)).convert('RGB')
        w, h = img.size
        samples = [
            img.getpixel((x, y))
            for x in range(w // 8, w // 3)
            for y in range(h // 4, 3 * h // 4)
        ]
        r, g, b = Counter(samples).most_common(1)[0][0]
        return f"#{r:02X}{g:02X}{b:02X}"
    except ImportError:
        print("  ⚠️  Pillow non installé — couleur non extraite (pip install Pillow)")
        return None
    except Exception as e:
        print(f"  ⚠️  Erreur extraction couleur : {e}")
        return None


def fetch_mediakit_chapter_assets(chapter_id):
    """
    Retourne {'logoURL': str, 'artworkURL': str, 'color': str} depuis
    le chapitre media kit Strapi (id fourni).
    """
    data = api_get(f"media-kit-chapters/{chapter_id}", {
        'locale':   'fr',
        'populate': 'deep',
    })
    if not data or 'data' not in data:
        print(f"  ⚠️  Impossible de récupérer le chapitre media kit {chapter_id}")
        return {}

    assets = {'logoURL': None, 'artworkURL': None, 'color': None}
    content = data['data']['attributes'].get('content', [])

    for block in content:
        if block.get('__component') != 'blocks.wysiwyg':
            continue
        html = block.get('content', '')

        # — Logo : cherche l'URL xlarge dans la section id="logo"
        logo_section = html[html.find('id="logo"'):]
        if logo_section:
            logo_match = re.search(
                r'(https://cdn\.starwarsunlimited\.com//xlarge_[^\s,\'"]+(?:Logo|logo)[^\s,\'"]+\.png)',
                logo_section,
            )
            if logo_match:
                assets['logoURL'] = logo_match.group(1)

        # — Artwork : cherche large_SWH_XX_Key_Art dans la section id="art"
        art_section = html[html.find('id="art"'):]
        if art_section:
            art_match = re.search(
                r'(https://cdn\.starwarsunlimited\.com//large_[^\s,\'"]*Key_Art[^\s,\'"]*\.jpg)',
                art_section,
            )
            if art_match:
                assets['artworkURL'] = art_match.group(1)

        # — Couleur : télécharge l'image swatch (xsmall) dans la section id="color"
        color_section = html[html.find('id="color"'):]
        if color_section:
            swatch_match = re.search(
                r'(https://cdn\.starwarsunlimited\.com//xsmall_[^\s,\'"]*(?:Color|color)[^\s,\'"]+\.png)',
                color_section,
            )
            if swatch_match:
                swatch_url = swatch_match.group(1)
                print(f"  🎨 Extraction couleur depuis {swatch_url.split('/')[-1]}...")
                assets['color'] = _extract_color_from_swatch(swatch_url)

    print(f"  📎 Assets media kit : {assets}")
    return assets


# ─── Helpers manifest ─────────────────────────────────────────────────────────

def load_manifest():
    with open(MANIFEST_PATH, encoding='utf-8') as f:
        return json.load(f)


def save_manifest(manifest):
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")


def known_api_codes(manifest):
    """
    {api_code (upper) → entrée manifest}
    Pour les entrées avec displayCode, l'api_code est le displayCode.
    """
    result = {}
    for s in manifest['sets']:
        api_code = s.get('displayCode', s['code']).upper()
        result[api_code] = s
    return result


def set_file_path(manifest_code):
    return os.path.join(SETS_DIR, f"{manifest_code.lower()}.json")


def save_set_json(manifest_code, cards):
    path = set_file_path(manifest_code)
    def has_variant_of(c):
        vo = c.get('attributes', {}).get('variantOf')
        return isinstance(vo, dict) and bool(vo.get('data'))
    data_cards    = [c for c in cards if not has_variant_of(c)]
    variant_cards = [c for c in cards if has_variant_of(c)]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"data": data_cards, "variants": variant_cards}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  💾 sets/{manifest_code.lower()}.json ({len(data_cards)} principales + {len(variant_cards)} variantes)")
    return path


# ─── Helpers announcements ────────────────────────────────────────────────────

def load_announcements():
    if not os.path.isfile(ANNOUNCEMENTS_PATH):
        return []
    with open(ANNOUNCEMENTS_PATH, encoding='utf-8') as f:
        return json.load(f)


def write_announcement_entry(title, color, changes_obj, detail=None):
    """
    Prépend une nouvelle annonce dans announcements.json.
    - title       : titre court affiché dans la liste (ex: "Nouvelles cartes ajoutées - LAW")
    - color       : hex couleur de l'en-tête du modal (ex: "#7C3AED")
    - changes_obj : {"added": [...codes...], "updated": [...codes...]} ou None par champ
    - detail      : texte long optionnel avec le détail carte par carte
    """
    today = date.today().isoformat()
    entry = {
        "id":       f"ann-{int(time.time())}",
        "title":    title,
        "detail":   detail,
        "color":    color or "#7C3AED",
        "url":      None,
        "imageURL": None,
        "date":     today,
        "changes":  changes_obj,
    }
    announcements = load_announcements()
    announcements.insert(0, entry)
    with open(ANNOUNCEMENTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(announcements, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  📣 Annonce écrite dans announcements.json : {title}")


# ─── Flatten pour comparaison ─────────────────────────────────────────────────

def _flatten(attrs):
    result = {}
    for key, val in attrs.items():
        if key in IGNORED_FIELDS:
            continue
        if isinstance(val, dict) and 'data' in val:
            data = val['data']
            if isinstance(data, dict):
                sub = data.get('attributes', data)
                result[key] = sub.get('name') or sub.get('id')
            elif isinstance(data, list):
                result[key] = [
                    ((item.get('attributes') or item).get('name') or item.get('id'))
                    for item in data
                ]
            else:
                result[key] = data
        else:
            result[key] = val
    return result


# ─── MODE SYNC ────────────────────────────────────────────────────────────────

def sync_mode():
    print("\n🔍 MODE SYNC — Détection de nouveaux sets")
    print("=" * 60)

    manifest = load_manifest()
    known    = known_api_codes(manifest)
    print(f"Sets connus ({len(known)}) : {sorted(known.keys())}\n")

    api_expansions  = fetch_all_api_expansions()
    print(f"\nExpansions API ({len(api_expansions)}) : {sorted(api_expansions.keys())}")

    print("\n📋 Chargement du media kit...")
    main_chapters = fetch_mediakit_main_chapters()

    new_codes = sorted(
        [c for c in api_expansions if c not in known],
        key=lambda c: api_expansions[c]['sortValue'],
    )

    if not new_codes:
        print("\n✅ Aucun nouveau set détecté.")
        return

    print(f"\n🆕 Nouveaux sets : {new_codes}")

    added      = []
    added_main = []

    for api_code in new_codes:
        exp_info = api_expansions[api_code]
        name     = exp_info['name']
        print(f"\n📦 Traitement de {api_code} — {name}")

        cards = fetch_cards(api_code)
        if not cards:
            print(f"  ⚠️  Aucune carte → set ignoré.")
            continue

        save_set_json(api_code, cards)

        name_lower = name.lower().strip()
        chapter_id = main_chapters.get(name_lower)
        is_main    = chapter_id is not None

        set_type    = "main" if is_main else "special"
        logo_url    = None
        artwork_url = None
        color       = "#888888"
        foil_shared = False

        if is_main:
            print(f"  🌟 Set MAIN détecté (chapter media kit id={chapter_id})")
            mk_assets   = fetch_mediakit_chapter_assets(chapter_id)
            logo_url    = mk_assets.get('logoURL')
            artwork_url = mk_assets.get('artworkURL')
            color       = mk_assets.get('color') or "#888888"

        same_type  = [s for s in manifest['sets'] if s.get('type') == set_type]
        next_order = max((s.get('order', 0) for s in same_type), default=0) + 1

        entry = {
            "id":          api_code.lower(),
            "dataVersion": 1,
            "code":        api_code,
            "name":        name,
            "type":        set_type,
            "order":       next_order,
            "color":       color,
            "foilShared":  foil_shared,
            "logoURL":     logo_url,
            "artworkURL":  artwork_url,
            "dataURL":     (
                f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
                f"/sets/{api_code.lower()}.json"
            ),
        }
        manifest['sets'].append(entry)
        added.append((api_code, name))
        if is_main:
            added_main.append(api_code)
            manifest['latestMainSet'] = api_code.lower()
        print(f"  ✅ {api_code} ({set_type}) ajouté au manifest.")

    if not added:
        print("\n⚠️  Aucun set effectivement ajouté.")
        return

    if len(added) == 1:
        api_code, name = added[0]
        ann_title = f"Nouveau set ajouté : {name} - {api_code}"
    else:
        codes_str = ", ".join(c for c, _ in added)
        ann_title = f"Nouveaux sets ajoutés : {codes_str}"

    ann_color = "#7C3AED"
    if len(added_main) == 1:
        for s in manifest['sets']:
            if s['code'] == added_main[0]:
                ann_color = s.get('color', ann_color)
                break

    changes_obj = {
        "added":   [c for c, _ in added],
        "updated": None,
    }
    write_announcement_entry(ann_title, ann_color, changes_obj)
    manifest['lastUpdated'] = date.today().isoformat()
    save_manifest(manifest)
    print(f"\n🎉 {len(added)} set(s) ajouté(s) : {[c for c, _ in added]}")
    if added_main:
        print(f"   Sets MAIN : {added_main} — latestMainSet mis à jour")


# ─── MODE COMPARE ─────────────────────────────────────────────────────────────

def compare_mode():
    print("\n🔄 MODE COMPARE — Comparaison des sets existants")
    print("=" * 60)

    manifest        = load_manifest()
    new_cards_items = []  # (set_code, card_number, card_name)
    rules_items     = []  # (set_code, card_number, card_name)
    other_sets      = []  # codes avec d'autres changements

    for entry in manifest['sets']:
        manifest_code = entry['code'].upper()
        api_code      = entry.get('displayCode', manifest_code).upper()

        if manifest_code in COMPARE_EXCLUDED:
            print(f"⏭️  {manifest_code} — exclu")
            continue

        local_file = set_file_path(manifest_code)
        if not os.path.isfile(local_file):
            print(f"⚠️  {manifest_code} — fichier local absent, ignoré")
            continue

        print(f"\n📊 {manifest_code} (code API : {api_code})...")

        with open(local_file, encoding='utf-8') as f:
            old_data = json.load(f)
        old_all   = old_data.get('data', []) + old_data.get('variants', [])
        old_cards = {str(c['id']): c for c in old_all if 'id' in c}

        new_cards_list = fetch_cards(api_code)
        if not new_cards_list:
            print(f"  ⚠️  Échec récupération — {manifest_code} non mis à jour")
            continue
        new_cards = {str(c['id']): c for c in new_cards_list if 'id' in c}

        added_ids   = [cid for cid in new_cards if cid not in old_cards]
        removed_ids = [cid for cid in old_cards if cid not in new_cards]

        changed = []
        for cid in new_cards:
            if cid not in old_cards:
                continue
            old_flat = _flatten(old_cards[cid].get('attributes', {}))
            new_flat = _flatten(new_cards[cid].get('attributes', {}))
            diffs = {
                k: (old_flat.get(k), new_flat.get(k))
                for k in (set(old_flat) | set(new_flat))
                if old_flat.get(k) != new_flat.get(k)
            }
            if diffs:
                changed.append((cid, diffs))

        if not added_ids and not removed_ids and not changed:
            print(f"  ✅ Aucun changement")
            time.sleep(1)
            continue

        if added_ids:
            print(f"  🆕 {len(added_ids)} nouvelle(s) carte(s) :")
            for cid in sorted(added_ids, key=int):
                attrs = new_cards[cid].get('attributes', {})
                num   = str(attrs.get('cardNumber', '?'))
                name  = attrs.get('title', '?')
                print(f"     + #{num} {name} (id:{cid})")
                new_cards_items.append((manifest_code, num, name))

        if removed_ids:
            print(f"  🗑️  {len(removed_ids)} carte(s) supprimée(s) :")
            for cid in sorted(removed_ids, key=int):
                attrs = old_cards[cid].get('attributes', {})
                print(f"     - #{attrs.get('cardNumber','?')} {attrs.get('title','?')} (id:{cid})")

        if changed:
            print(f"  ✏️  {len(changed)} carte(s) modifiée(s) :")
            has_other = False
            for cid, diffs in sorted(changed, key=lambda x: int(x[0])):
                attrs = new_cards[cid].get('attributes', {})
                num   = str(attrs.get('cardNumber', '?'))
                name  = attrs.get('title', '?')
                print(f"     ~ #{num} {name} (id:{cid}) — champs : {list(diffs.keys())}")
                if 'rules' in diffs:
                    rules_items.append((manifest_code, num, name))
                else:
                    has_other = True
            if has_other:
                other_sets.append(manifest_code)

        save_set_json(manifest_code, new_cards_list)
        entry['dataVersion'] = entry.get('dataVersion', 1) + 1

        time.sleep(2)

    if not new_cards_items and not rules_items and not other_sets:
        print("\n✅ Aucun changement détecté dans les sets existants.")
        return

    new_cards_sets = list(dict.fromkeys(c for c, _, _ in new_cards_items))
    rules_sets     = list(dict.fromkeys(c for c, _, _ in rules_items))

    parts = []
    if new_cards_sets:
        parts.append(f"Nouvelles cartes ajoutées - {', '.join(new_cards_sets)}")
    if rules_sets:
        parts.append(f"Règles mises à jour - {', '.join(rules_sets)}")
    if other_sets and not new_cards_sets and not rules_sets:
        parts.append(f"Données mises à jour - {', '.join(other_sets)}")
    ann_title = " | ".join(parts) if parts else "Données mises à jour"

    detail_parts = []
    if new_cards_items:
        lines = ["Nouvelles cartes :"]
        for set_code, num, name in new_cards_items:
            lines.append(f"- {set_code} {num} - {name}")
        detail_parts.append("\n".join(lines))
    if rules_items:
        lines = ["Regles mises a jour :"]
        for set_code, num, name in rules_items:
            lines.append(f"- {set_code} {num} - {name}")
        detail_parts.append("\n".join(lines))
    detail = "\n\n".join(detail_parts) if detail_parts else None

    changes_obj = {
        "added":   new_cards_sets or None,
        "updated": rules_sets or None,
    }

    write_announcement_entry(ann_title, "#7C3AED", changes_obj, detail)
    manifest['lastUpdated'] = date.today().isoformat()
    save_manifest(manifest)
    print(f"\n🎉 Changements : {len(new_cards_items)} nouvelles cartes, {len(rules_items)} règles.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Synchronisation automatique des sets SWU")
    parser.add_argument(
        '--mode', choices=['sync', 'compare'], required=True,
        help="sync: nouveaux sets | compare: mise à jour sets existants",
    )
    args = parser.parse_args()
    if args.mode == 'sync':
        sync_mode()
    else:
        compare_mode()


if __name__ == '__main__':
    main()
