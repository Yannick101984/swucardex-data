#!/usr/bin/env python3
"""
Génération de cardmarket_prices.json pour SWUCardex.
Usage : python3 generate_prices.py [--repo-root /chemin/vers/swucardex-data]

Structure attendue dans le repo :
  cardmarket/products_singles.json   — catalogue Cardmarket (téléchargé depuis CM)
  cardmarket/price_guide.json        — prix Cardmarket (téléchargé depuis CM)
  sets/sor.json, sets/law.json …     — JSONs des sets SWU
  prices/cardmarket_prices.json      — GÉNÉRÉ par ce script

Appelé automatiquement par GitHub Actions à chaque push sur cardmarket/*.json
"""
import argparse, sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo-root", default=".", help="Chemin racine du repo swucardex-data")
args, _ = parser.parse_known_args()
REPO_ROOT = args.repo_root

import json, os, re, unicodedata
from collections import defaultdict, Counter

# ── Chemins (relatifs à la racine du repo) ────────────────────────────────────
CM_DIR   = os.path.join(REPO_ROOT, "cardmarket")
SETS_DIR = os.path.join(REPO_ROOT, "sets")
OUT_DIR  = os.path.join(REPO_ROOT, "prices")

SET_FILES = {
    "SOR":"sor.json","SHD":"shd.json","TWI":"twi.json","JTL":"jtl.json",
    "LOF":"lof.json","SEC":"sec.json","LAW":"law.json",
    "SECW":"secw.json","JTLW":"jtlw.json","LOFW":"lofw.json",
    "C24":"c24.json","C25":"c25.json","IBH":"ibh.json","GG":"gg.json",
    "J24":"j24.json","J25":"j25.json","P25":"p25.json","P26":"p26.json","TS26":"ts26.json",
}

# Correspondance idExpansion → set_code — CONFIRMÉE (ne pas modifier manuellement)
# Régénérée automatiquement si de nouveaux idExpansion apparaissent dans les fichiers CM.
# expansion_role: "standard" = uniquement cartes standard non-foil
#                 "variants" = hyperspace + foils + showcase + prestige
#                 "special"  = sets spéciaux/promos
EXPANSION_MAP_CONFIRMED = {
    5618: ("SOR", "standard"), 5638: ("SOR", "variants"), 5626: ("SOR", "special"), 5688: ("SOR", "special"),
    5769: ("SHD", "standard"), 5781: ("SHD", "variants"), 5839: ("SHD", "special"),
    5888: ("TWI", "standard"), 5937: ("TWI", "variants"), 5939: ("TWI", "special"),
    5995: ("JTL", "standard"), 6074: ("JTL", "variants"), 6075: ("JTL", "special"), 6023: ("JTL", "special"),
    6105: ("LOF", "standard"), 6188: ("LOF", "variants"), 6206: ("LOF", "special"), 6418: ("LOF", "special"),
    6101: ("P25", "standard"),
    6268: ("IBH", "standard"),
    6333: ("SEC", "standard"), 6364: ("SEC", "variants"), 6386: ("SEC", "special"),
    6451: ("LAW", "standard"), 6452: ("LAW", "variants"), 6453: ("LAW", "special"),
    6472: ("P26", "standard"),
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def normalize(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s)

def cm_name_to_card_name(cm_name):
    """Extrait le nom de la carte depuis le nom CM.
    Cas 1 - Base + Token : 'Capital City // Experience Token' → 'Capital City'
    Cas 2 - Leader double face : 'Chancellor Palpatine // Darth Sidious, Playing Both Sides'
             → 'Chancellor Palpatine, Playing Both Sides'
             (premier titre + sous-titre de la seconde face)
    """
    if "//" not in cm_name:
        return cm_name.strip()
    left, right = [s.strip() for s in cm_name.split("//", 1)]
    # Si la partie droite contient une virgule c'est une double-face avec sous-titre
    if "," in right:
        subtitle = right.split(",", 1)[1].strip()
        return f"{left}, {subtitle}"
    # Sinon c'est Base // Token : garder seulement la partie gauche
    return left

def price_entry(price_dict):
    """Construit un dict de prix propre depuis une entrée priceGuide."""
    if not price_dict:
        return None
    avg      = price_dict.get("avg")
    low      = price_dict.get("low")
    trend    = price_dict.get("trend")
    avg_foil = price_dict.get("avg-foil")
    low_foil = price_dict.get("low-foil")
    trend_foil = price_dict.get("trend-foil")
    avg1     = price_dict.get("avg1")
    avg7     = price_dict.get("avg7")
    avg30    = price_dict.get("avg30")
    avg1f    = price_dict.get("avg1-foil")
    avg7f    = price_dict.get("avg7-foil")
    avg30f   = price_dict.get("avg30-foil")
    return {
        "avg": avg, "low": low, "trend": trend,
        "avg_foil": avg_foil, "low_foil": low_foil, "trend_foil": trend_foil,
        "avg1": avg1, "avg7": avg7, "avg30": avg30,
        "avg1_foil": avg1f, "avg7_foil": avg7f, "avg30_foil": avg30f,
    }

def is_foil_only(price_dict):
    """Produit vendu uniquement en foil (pas de prix non-foil)."""
    return price_dict.get("avg") is None and price_dict.get("avg-foil") is not None

def is_nonfoil_only(price_dict):
    """Produit vendu uniquement en non-foil."""
    return price_dict.get("avg") is not None and price_dict.get("avg-foil") is None

def get_en_name(attrs):
    """Récupère le nom EN d'une carte depuis ses localisations."""
    locs = attrs.get("localizations", {})
    loc_data = locs.get("data", []) if isinstance(locs, dict) else []
    for loc in loc_data:
        la = loc.get("attributes", {})
        if la.get("locale") == "en":
            title    = (la.get("title")    or "").strip()
            subtitle = (la.get("subtitle") or "").strip()
            return f"{title}, {subtitle}" if subtitle else title
    # Fallback titre principal
    title    = (attrs.get("title")    or "").strip()
    subtitle = (attrs.get("subtitle") or "").strip()
    return f"{title}, {subtitle}" if subtitle else title

# ── Chargement Cardmarket ─────────────────────────────────────────────────────
print("=" * 65)
print("Chargement Cardmarket...")

with open(f"{CM_DIR}/products_singles.json") as f:
    singles_data = json.load(f)
with open(f"{CM_DIR}/price_guide.json") as f:
    prices_data = json.load(f)

cm_products = {p["idProduct"]: p for p in singles_data["products"]}
cm_prices   = {p["idProduct"]: p for p in prices_data["priceGuides"]}

print(f"  {len(cm_products)} singles | {len(cm_prices)} prix | snapshot: {prices_data['createdAt']}")

# ── Auto-détection des nouvelles expansions ───────────────────────────────────
# Charger les sets SWU pour le matching (index nom EN normalisé)
def _build_swu_name_index():
    idx = {}  # set_code → set of norm_en_names
    for sc, fname in SET_FILES.items():
        path = f"{SETS_DIR}/{fname}"
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)
        cards = data.get("data", data) if isinstance(data, dict) else data
        names = set()
        for card in cards:
            attrs = card.get("attributes", card)
            names.add(normalize(get_en_name(attrs)))
        idx[sc] = names
    return idx

_swu_name_idx = _build_swu_name_index()

all_exp_ids = set(p["idExpansion"] for p in singles_data["products"])
new_exp_ids = all_exp_ids - set(EXPANSION_MAP_CONFIRMED.keys())

EXPANSION_MAP = dict(EXPANSION_MAP_CONFIRMED)  # copie de travail

if new_exp_ids:
    print(f"\n  ⚠️  {len(new_exp_ids)} nouvelle(s) expansion(s) CM détectée(s) — auto-mapping :")
    # Pour chaque nouvelle expansion, trouver le meilleur set SWU par overlap de noms
    from collections import Counter as _C
    exp_prods = {eid: [p for p in singles_data["products"] if p["idExpansion"] == eid]
                 for eid in new_exp_ids}
    for exp_id in sorted(new_exp_ids):
        cm_names = {normalize(p["name"].split("//")[0].strip()) for p in exp_prods[exp_id]}
        best_set, best_score = None, 0
        for sc, swu_names in _swu_name_idx.items():
            score = len(cm_names & swu_names)
            if score > best_score:
                best_score, best_set = score, sc
        n_prods = len(exp_prods[exp_id])
        pct = best_score / len(cm_names) * 100 if cm_names else 0
        # Rôle : si toutes les expansions connues pour ce set ont déjà un "standard"
        # et que le count est plus grand → "variants", sinon "standard"
        known_for_set = [(eid, role) for eid, (sc, role) in EXPANSION_MAP.items() if sc == best_set]
        has_standard = any(r == "standard" for _, r in known_for_set)
        role = "variants" if has_standard else "standard"
        if best_score > 0:
            EXPANSION_MAP[exp_id] = (best_set, role)
            print(f"    idExpansion {exp_id:6d} ({n_prods:4d} prods) → {best_set} [{role}]  ({best_score} matchs, {pct:.0f}%) ← À CONFIRMER")
        else:
            print(f"    idExpansion {exp_id:6d} ({n_prods:4d} prods) → ??? (0 matchs — set inconnu, ignoré)")
else:
    print("  ✅ Aucune nouvelle expansion CM.")

# ── Chargement SWU ────────────────────────────────────────────────────────────
print("\nChargement sets SWU...")

# Index : (set_code, norm_en_name) → {card_number, list of variant_types disponibles}
swu_index = defaultdict(lambda: {"card_number": None, "variant_types": set(), "en_name": ""})
swu_card_types = {}  # (set_code, norm_en_name) → type de carte (Leader, Unité, Base…)

for set_code, fname in SET_FILES.items():
    path = f"{SETS_DIR}/{fname}"
    if not os.path.exists(path):
        continue
    with open(path) as f:
        data = json.load(f)
    cards = data.get("data", data) if isinstance(data, dict) else data
    for card in cards:
        attrs = card.get("attributes", card)
        en_name = get_en_name(attrs)
        norm    = normalize(en_name)
        card_number = attrs.get("cardNumber")
        vt_data = attrs.get("variantTypes", {}).get("data", [])
        variant_type = vt_data[0]["attributes"]["name"] if vt_data else "Standard"
        card_type_obj = attrs.get("type", {})
        if isinstance(card_type_obj, dict):
            card_type = card_type_obj.get("data", {}).get("attributes", {}).get("name", "")
        else:
            card_type = ""

        key = (set_code, norm)
        swu_index[key]["card_number"] = card_number
        swu_index[key]["en_name"]     = en_name
        swu_index[key]["variant_types"].add(variant_type)
        swu_card_types[key] = card_type

total_cards = len(swu_index)
print(f"  {total_cards} cartes uniques (nom × set)")

# ── Groupement des produits CM par (set_code, norm_en_name) ──────────────────
print("\nGroupement produits CM...")

# cm_by_card[(set_code, norm_name)] = {
#     "standard": [list of (idProduct, price_dict)],
#     "variants": [list of (idProduct, price_dict)],
#     "special":  [list of (idProduct, price_dict)],
# }
cm_by_card = defaultdict(lambda: {"standard": [], "variants": [], "special": []})

unmapped_exp = Counter()
for prod in singles_data["products"]:
    exp_id = prod["idExpansion"]
    if exp_id not in EXPANSION_MAP:
        unmapped_exp[exp_id] += 1
        continue
    set_code, role = EXPANSION_MAP[exp_id]
    card_name = cm_name_to_card_name(prod["name"])
    norm = normalize(card_name)
    price = cm_prices.get(prod["idProduct"], {})
    cm_by_card[(set_code, norm)][role].append((prod["idProduct"], price, prod["name"]))

if unmapped_exp:
    print(f"  ⚠️  Expansions non mappées: {dict(unmapped_exp)}")

# ── Construction de la table de prix ─────────────────────────────────────────
print("\nConstruction table de prix...")

price_table = []  # résultat final
unmatched   = []  # cartes SWU sans données CM
no_swu_card = []  # produits CM sans carte SWU correspondante

for key, swu_info in swu_index.items():
    set_code, norm = key
    en_name      = swu_info["en_name"]
    card_number  = swu_info["card_number"]
    variant_types = swu_info["variant_types"]
    card_type    = swu_card_types.get(key, "")
    cm_data      = cm_by_card.get(key, {"standard": [], "variants": [], "special": []})

    prices_out = {}

    # ── Expansion Standard ──────────────────────────────────────────────────
    # Habituellement 1 seul produit par carte dans l'expansion standard.
    # Pour SOR/SHD/TWI le même produit peut avoir avg (Standard) + avg-foil (Standard Foil).
    for idp, pr, _ in cm_data["standard"]:
        if is_foil_only(pr):
            # Produit foil dans l'expansion standard (rare)
            if "standard_foil" not in prices_out:
                prices_out["standard_foil"] = {"idProduct": idp, **price_entry(pr)}
        else:
            # Produit Standard (peut aussi avoir un avg-foil = Standard Foil sur anciens sets)
            if "standard" not in prices_out:
                prices_out["standard"] = {"idProduct": idp, **price_entry(pr)}
            # Sur anciens sets (SOR/SHD/TWI), avg-foil dans le même produit = Standard Foil
            if pr.get("avg-foil") and "standard_foil" not in prices_out:
                prices_out["standard_foil"] = {"idProduct": idp, **price_entry(pr)}

    # ── Expansion Variants ──────────────────────────────────────────────────
    # Trier par idProduct croissant (ordre de création = Standard < Hyperspace < Prestige)
    variants_sorted = sorted(cm_data["variants"], key=lambda x: x[0])

    foil_prods    = [(idp, pr) for idp, pr, _ in variants_sorted if is_foil_only(pr)]
    nonfoil_prods = [(idp, pr) for idp, pr, _ in variants_sorted if is_nonfoil_only(pr)]
    both_prods    = [(idp, pr) for idp, pr, _ in variants_sorted
                     if not is_foil_only(pr) and not is_nonfoil_only(pr)]

    is_leader = card_type == "Leader"

    # Rang 0 → Hyperspace / Showcase (Leaders)
    # Rang 1 → Standard Prestige
    for rank, (idp, pr) in enumerate(nonfoil_prods + both_prods):
        avg = pr.get("avg")
        if avg is None:
            continue
        if rank == 0:
            key = "showcase" if is_leader else "hyperspace"
        elif rank == 1:
            key = "standard_prestige"
        else:
            break
        if key not in prices_out:
            prices_out[key] = {"idProduct": idp, **price_entry(pr)}

    # Foil rang 0 → Hyperspace Foil ; rang 1 → Foil Prestige ; rang 2 → Serialized Prestige
    foil_keys = ["hyperspace_foil", "foil_prestige", "serialized_prestige"]
    for rank, (idp, pr) in enumerate(foil_prods):
        if rank >= len(foil_keys):
            break
        key = foil_keys[rank]
        if key not in prices_out:
            prices_out[key] = {"idProduct": idp, **price_entry(pr)}

    # Anciens sets (SOR/SHD/TWI) — produits "both" : avg-foil → Hyperspace Foil si non renseigné
    for idp, pr in both_prods:
        if pr.get("avg-foil") and "hyperspace_foil" not in prices_out and not is_leader:
            prices_out["hyperspace_foil"] = {"idProduct": idp, **price_entry(pr)}

    # ── Special/promos ──────────────────────────────────────────────────────
    # On les ignore pour les prix principaux (variant_type exotiques)

    if prices_out:
        entry = {
            "set_code": set_code,
            "en_name": en_name,
            "card_number": card_number,
            "card_type": card_type,
            "variant_types_in_app": sorted(variant_types),
            "prices": prices_out,
        }
        price_table.append(entry)
    else:
        unmatched.append({
            "set_code": set_code, "en_name": en_name,
            "card_number": card_number, "variant_types": sorted(variant_types)
        })

# ── Rapport ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("RÉSULTATS")
print(f"  Cartes avec prix     : {len(price_table)}")
print(f"  Cartes sans prix CM  : {len(unmatched)}")
print(f"  Couverture           : {len(price_table)/total_cards*100:.1f}%")

# Par set
from collections import Counter as C
by_set_total   = C(k[0] for k in swu_index.keys())
by_set_matched = C(e["set_code"] for e in price_table)
print("\n  Par set :")
for sc in sorted(by_set_total.keys()):
    total   = by_set_total[sc]
    matched = by_set_matched.get(sc, 0)
    bar = "█" * int(matched / total * 20) + "░" * (20 - int(matched / total * 20))
    print(f"    {sc:6s} {bar} {matched:4d}/{total:4d} ({matched/total*100:.0f}%)")

# Types de prix disponibles
price_keys_count = Counter()
for e in price_table:
    for k in e["prices"]:
        price_keys_count[k] += 1
print("\n  Prix disponibles par type :")
for k, count in price_keys_count.most_common():
    print(f"    {k:20s}: {count:5d} cartes")

# Cartes SWU non matchées (échantillon)
if unmatched:
    print(f"\n  Non matchées ({len(unmatched)}) — échantillon :")
    # Grouper par raison probable
    tokens = [u for u in unmatched if u["en_name"] in ("Experience", "Shield", "Force", "Credit")]
    bases  = [u for u in unmatched if "Base" in " ".join(u["variant_types"])]
    other  = [u for u in unmatched if u not in tokens and u not in bases]
    if tokens:   print(f"    Tokens (Experience/Shield…) : {len(tokens)} — normal, pas de prix CM")
    if bases:    print(f"    Bases sans match           : {len(bases)}")
    for u in other[:10]:
        print(f"    {u['set_code']:6s} #{u['card_number']:4} {u['en_name'][:50]}")

# ── Export prix courants ──────────────────────────────────────────────────────
price_date = prices_data.get("createdAt", "")
# Extraire juste la date YYYY-MM-DD
snapshot_date = price_date[:10] if price_date else "unknown"

output = {
    "version": 1,
    "priceDate": price_date,
    "expansion_map": {str(k): {"set_code": v[0], "role": v[1]} for k, v in EXPANSION_MAP.items()},
    "prices": price_table,
}
os.makedirs(OUT_DIR, exist_ok=True)
out_path = f"{OUT_DIR}/cardmarket_prices.json"
with open(out_path, "w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n✅ {out_path} exporté ({len(price_table)} entrées)")

# ── Export historique (accumulation des snapshots) ────────────────────────────
# Format compact pour l'historique : seulement avg par variante pour économiser l'espace
def compact_snapshot(entries):
    """Réduit chaque entrée à {set_code|en_name: {variant: avg}}."""
    result = {}
    for e in entries:
        key = f"{e['set_code']}|{e['en_name']}"
        result[key] = {
            vt: (pr.get("avg") or pr.get("avg_foil"))
            for vt, pr in e["prices"].items()
            if (pr.get("avg") or pr.get("avg_foil")) is not None
        }
    return result

history_path = f"{OUT_DIR}/cardmarket_prices_history.json"

# Charger l'historique existant (ou créer un nouveau)
if os.path.exists(history_path):
    with open(history_path) as f:
        history = json.load(f)
else:
    history = {"version": 1, "snapshots": []}

# Vérifier si ce snapshot existe déjà (même date)
existing_dates = {s["date"] for s in history["snapshots"]}
if snapshot_date in existing_dates:
    print(f"  ℹ️  Snapshot {snapshot_date} déjà présent dans l'historique — mise à jour")
    history["snapshots"] = [s for s in history["snapshots"] if s["date"] != snapshot_date]

# Ajouter le nouveau snapshot
history["snapshots"].append({
    "date": snapshot_date,
    "prices": compact_snapshot(price_table),
})

# Trier par date croissante
history["snapshots"].sort(key=lambda s: s["date"])

with open(history_path, "w") as f:
    json.dump(history, f, ensure_ascii=False, separators=(",", ":"))  # compact pour minimiser la taille

nb_snapshots = len(history["snapshots"])
dates = [s["date"] for s in history["snapshots"]]
print(f"✅ {history_path} mis à jour ({nb_snapshots} snapshot(s) : {', '.join(dates)})")

print("\nFin.")
