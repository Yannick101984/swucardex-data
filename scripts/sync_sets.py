#!/usr/bin/env python3
"""
Synchronisation automatique des sets Star Wars Unlimited.

Modes :
  sync    — Détecte les nouveaux sets, scrape les cartes, ajoute les JSONs,
             met à jour manifest.json (annonce + entrée de set).
  compare — Compare les sets existants (hors SOR/TWI/SHD) avec l'API,
             met à jour les JSONs si des différences sont trouvées,
             bumpe dataVersion dans le manifest et met à jour l'annonce.
"""

import argparse
import json
import os
import sys
import time
import requests
from datetime import date

# ─── Paths ────────────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))
SETS_DIR      = os.path.join(_HERE, '..', 'sets')
MANIFEST_PATH = os.path.join(_HERE, '..', 'manifest.json')

# ─── Config ───────────────────────────────────────────────────────────────────
REPO   = "Yannick101984/swucardex-data"
BRANCH = os.environ.get("GITHUB_REF_NAME", "staging")

# Sets exclus de la comparaison (données figées côté app)
COMPARE_EXCLUDED = {'SOR', 'TWI', 'SHD'}

# Champs ignorés lors de la comparaison (HTML rendu, timestamps internes)
IGNORED_FIELDS = {
    'textStyled', 'deployBoxStyled', 'epicActionStyled',
    'rulesStyled', 'updatedAt', 'createdAt', 'linkHtml',
}

API_BASE = "https://admin.starwarsunlimited.com/api"
HEADERS  = {
    'User-Agent':      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept':          'application/json',
    'Accept-Language': 'fr-FR,fr;q=0.9',
    'Referer':         'https://starwarsunlimited.com/',
    'Origin':          'https://starwarsunlimited.com',
}


# ─── Helpers API ──────────────────────────────────────────────────────────────

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


def fetch_cards(api_code):
    """Récupère toutes les cartes d'une expansion via son code API."""
    print(f"  🃏 Scraping {api_code}...")
    all_cards = []
    page = 1
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
            print(f"  ❌ Impossible de récupérer la page {page} pour {api_code}")
            break
        cards = data['data']
        if not cards:
            break
        all_cards.extend(cards)
        meta    = data.get('meta', {}).get('pagination', {})
        current = meta.get('page', page)
        last    = meta.get('pageCount', 0)
        total   = meta.get('total', 0)
        print(f"  Page {current}/{last} — {len(cards)} cartes  (total API: {total})")
        if last and current >= last:
            break
        page += 1
        time.sleep(2)
        if page > 100:
            print("  ⚠️  Sécurité : >100 pages, arrêt")
            break
    print(f"  ✅ {len(all_cards)} cartes récupérées pour {api_code}")
    return all_cards


def fetch_all_api_expansions():
    """
    Retourne un dict {api_code (upper): {'name': str, 'sortValue': int}}
    en requêtant les cartes avec cardNumber <= 10 (couvre tous les sets,
    même ceux qui commencent à un numéro > 1).
    """
    found = {}
    page  = 1
    while True:
        params = {
            'locale':                                   'fr',
            'pagination[pageSize]':                    '250',
            'pagination[page]':                        str(page),
            'filters[$and][0][cardNumber][$lte]':      '10',
            'fields[0]':                               'cardNumber',
            'populate[expansion][fields][0]':          'code',
            'populate[expansion][fields][1]':          'name',
            'populate[expansion][fields][2]':          'sortValue',
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


# ─── Helpers manifest ─────────────────────────────────────────────────────────

def load_manifest():
    with open(MANIFEST_PATH, encoding='utf-8') as f:
        return json.load(f)


def save_manifest(manifest):
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write('\n')


def known_api_codes(manifest):
    """
    Retourne un dict {api_code (upper) → entrée manifest}.
    Pour les entrées avec displayCode, l'api_code est le displayCode ;
    sinon c'est le champ code.
    """
    result = {}
    for s in manifest['sets']:
        api_code = s.get('displayCode', s['code']).upper()
        result[api_code] = s
    return result


def set_file_path(manifest_code):
    """Chemin du fichier JSON local pour un set (code interne manifest)."""
    return os.path.join(SETS_DIR, f"{manifest_code.lower()}.json")


def save_set_json(manifest_code, cards):
    path = set_file_path(manifest_code)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"data": cards}, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f"  💾 sets/{manifest_code.lower()}.json ({len(cards)} cartes)")
    return path


def update_announcement(manifest, text, color=None):
    today = date.today().isoformat()
    manifest['announcement'] = {
        "id":    f"ann-{today}",
        "text":  text,
        "color": color or manifest.get('announcement', {}).get('color', '#3B82F6'),
        "url":   "",
    }
    manifest['lastUpdated'] = today


# ─── Flatten pour comparaison ─────────────────────────────────────────────────

def _flatten(attrs):
    """Aplatit les attributs Strapi en dict simple pour comparaison."""
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

    api_expansions = fetch_all_api_expansions()
    print(f"\nExpansions API détectées ({len(api_expansions)}) : {sorted(api_expansions.keys())}\n")

    new_codes = sorted(
        [c for c in api_expansions if c not in known],
        key=lambda c: api_expansions[c]['sortValue'],
    )

    if not new_codes:
        print("✅ Aucun nouveau set détecté.")
        return

    print(f"🆕 Nouveaux sets : {new_codes}")

    added = []
    for api_code in new_codes:
        exp_info = api_expansions[api_code]
        name     = exp_info['name']
        print(f"\n📦 Traitement de {api_code} — {name}")

        cards = fetch_cards(api_code)
        if not cards:
            print(f"  ⚠️  Aucune carte trouvée → set ignoré.")
            continue

        # Sauvegarde du JSON de cartes
        save_set_json(api_code, cards)

        # Ordre : max des ordres dans le type "special" + 1 (type par défaut)
        same_type = [s for s in manifest['sets'] if s.get('type') == 'special']
        next_order = max((s.get('order', 0) for s in same_type), default=0) + 1

        entry = {
            "id":          api_code.lower(),
            "dataVersion": 1,
            "code":        api_code,
            "name":        name,
            "type":        "special",
            "order":       next_order,
            "color":       "#888888",
            "foilShared":  False,
            "logoURL":     None,
            "artworkURL":  None,
            "dataURL":     (
                f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
                f"/sets/{api_code.lower()}.json"
            ),
        }
        manifest['sets'].append(entry)
        added.append((api_code, name))
        print(f"  ✅ {api_code} ajouté au manifest.")

    if not added:
        print("\n⚠️  Aucun set effectivement ajouté.")
        return

    # Mise à jour de l'annonce
    if len(added) == 1:
        api_code, name = added[0]
        ann_text = f"Nouveau set ajouté : {name} - {api_code}"
    else:
        codes_str = ", ".join(c for c, _ in added)
        ann_text  = f"Nouveaux sets ajoutés : {codes_str}"

    update_announcement(manifest, ann_text)
    save_manifest(manifest)
    print(f"\n🎉 {len(added)} set(s) ajouté(s) : {[c for c, _ in added]}")


# ─── MODE COMPARE ─────────────────────────────────────────────────────────────

def compare_mode():
    print("\n🔄 MODE COMPARE — Comparaison des sets existants")
    print("=" * 60)

    manifest        = load_manifest()
    changes_summary = []  # list de tuples (type, manifest_code, count)

    for entry in manifest['sets']:
        manifest_code = entry['code'].upper()
        api_code      = entry.get('displayCode', manifest_code).upper()

        if manifest_code in COMPARE_EXCLUDED:
            print(f"⏭️  {manifest_code} — exclu de la comparaison")
            continue

        local_file = set_file_path(manifest_code)
        if not os.path.isfile(local_file):
            print(f"⚠️  {manifest_code} — fichier local absent, ignoré")
            continue

        print(f"\n📊 {manifest_code} (code API : {api_code})...")

        with open(local_file, encoding='utf-8') as f:
            old_data = json.load(f)
        old_cards = {str(c['id']): c for c in old_data.get('data', []) if 'id' in c}

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

        # Affichage détaillé des différences
        if added_ids:
            print(f"  🆕 {len(added_ids)} nouvelle(s) carte(s) :")
            for cid in sorted(added_ids, key=int):
                attrs = new_cards[cid].get('attributes', {})
                print(f"     + #{attrs.get('cardNumber','?')} {attrs.get('title','?')} (id:{cid})")

        if removed_ids:
            print(f"  🗑️  {len(removed_ids)} carte(s) supprimée(s) :")
            for cid in sorted(removed_ids, key=int):
                attrs = old_cards[cid].get('attributes', {})
                print(f"     - #{attrs.get('cardNumber','?')} {attrs.get('title','?')} (id:{cid})")

        if changed:
            print(f"  ✏️  {len(changed)} carte(s) modifiée(s) :")
            for cid, diffs in sorted(changed, key=lambda x: int(x[0])):
                attrs = new_cards[cid].get('attributes', {})
                print(f"     ~ #{attrs.get('cardNumber','?')} {attrs.get('title','?')} "
                      f"(id:{cid}) — champs : {list(diffs.keys())}")

        # Mise à jour du JSON local
        save_set_json(manifest_code, new_cards_list)

        # Bump dataVersion
        entry['dataVersion'] = entry.get('dataVersion', 1) + 1

        # Accumulation pour l'annonce
        if added_ids:
            changes_summary.append(('new_cards', manifest_code, len(added_ids)))
        if changed:
            rules_changed = [cid for cid, d in changed if 'rules' in d]
            if rules_changed:
                changes_summary.append(('rules', manifest_code, len(rules_changed)))
            other = len(changed) - len(rules_changed)
            if other:
                changes_summary.append(('updated', manifest_code, other))

        time.sleep(2)

    if not changes_summary:
        print("\n✅ Aucun changement détecté dans les sets existants.")
        return

    # Construction du texte d'annonce
    new_cards_sets = [c for t, c, _ in changes_summary if t == 'new_cards']
    rules_sets     = [c for t, c, _ in changes_summary if t == 'rules']
    updated_sets   = [c for t, c, _ in changes_summary if t == 'updated']

    parts = []
    if new_cards_sets:
        parts.append(f"Nouvelles cartes ajoutées - {', '.join(new_cards_sets)}")
    if rules_sets:
        parts.append(f"Règles mises à jour - {', '.join(rules_sets)}")
    if updated_sets and not rules_sets:
        parts.append(f"Données mises à jour - {', '.join(updated_sets)}")

    ann_text = " | ".join(parts) if parts else "Données mises à jour"
    update_announcement(manifest, ann_text)
    save_manifest(manifest)
    print(f"\n🎉 Changements appliqués : {changes_summary}")


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
