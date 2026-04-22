#!/usr/bin/env python3
"""
Génération de cardmarket_prices.json pour SWUCardex.
Usage : python3 generate_prices.py [--repo-root /chemin/vers/swucardex-data]

Algorithmes de matching :
  - Anciens sets (SOR/SHD/TWI) : un produit CM couvre foil + non-foil → split foil/non-foil
  - Nouveaux sets (JTL+) : matching positionnel par numéro de carte SWU
  - Weekly Play (JTLW/LOFW/SECW/LAWP) : NF → weekly_play, FOIL → weekly_play_foil
  - Sets spéciaux multi-set (C24/C25/GG/J24/J25/P25/P26) : lookup par nom

Appelé automatiquement par GitHub Actions à chaque push sur cardmarket/*.json
"""
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--repo-root", default=".", help="Chemin racine du repo swucardex-data")
parser.add_argument("--verbose", action="store_true", help="Afficher les détails de mapping")
args, _ = parser.parse_known_args()
REPO_ROOT = args.repo_root
VERBOSE   = args.verbose

import json, os, re, unicodedata
from collections import defaultdict, Counter

# ── Chemins ───────────────────────────────────────────────────────────────────
CM_DIR   = os.path.join(REPO_ROOT, "cardmarket")
SETS_DIR = os.path.join(REPO_ROOT, "sets")
OUT_DIR  = os.path.join(REPO_ROOT, "prices")

SET_FILES = {
    # Sets principaux
    "SOR":"sor.json","SHD":"shd.json","TWI":"twi.json","JTL":"jtl.json",
    "LOF":"lof.json","SEC":"sec.json","LAW":"law.json",
    # Weekly Play
    "JTLW":"jtlw.json","LOFW":"lofw.json","SECW":"secw.json","LAWP":"lawp.json",
    # Sets spéciaux
    "C24":"c24.json","C25":"c25.json","GG":"gg.json",
    "J24":"j24.json","J25":"j25.json",
    "P25":"p25.json","P26":"p26.json",
    "IBH":"ibh.json","TS26":"ts26.json",
}

# Sets avec foil+non-foil sur le même produit CM (ancien format)
OLD_SETS         = {"SOR", "SHD", "TWI"}
# Sets Weekly Play : NF → weekly_play / FOIL → weekly_play_foil
WEEKLY_PLAY_SETS = {"JTLW", "LOFW", "SECW", "LAWP"}
# Sets spéciaux "purs" : toutes leurs cartes ont un type de variante non-standard
SPECIAL_PURE_SETS = {"C24", "C25", "GG", "J24", "J25", "P25", "P26", "IBH", "TS26"}

# ── Correspondance idExpansion → (set_code, role) ─────────────────────────────
# Rôles :
#   "standard"  → expansion principale (Standard + Standard Foil pour les nouveaux sets)
#   "variants"  → expansion variantes (Hyperspace, Prestige, Showcase, etc.)
#   "multi"     → expansion multi-sets → lookup par nom dans plusieurs sets spéciaux
#   "ignored"   → promos store non présentes dans les JSON SWU (5688/5839/5939)
EXPANSION_MAP_CONFIRMED = {
    # SOR
    5618: ("SOR",  "standard"), 5638: ("SOR",  "variants"),
    5626: ("SOR",  "multi"),    # C24 + C25 + GG mélangés
    5688: ("SOR",  "ignored"),  # Promos store SOR non trackées dans SWU
    # SHD
    5769: ("SHD",  "standard"), 5781: ("SHD",  "variants"),
    5839: ("SHD",  "ignored"),  # Promos store SHD
    # TWI
    5888: ("TWI",  "standard"), 5937: ("TWI",  "variants"),
    5939: ("TWI",  "ignored"),  # Promos store TWI
    # JTL
    5995: ("JTL",  "standard"), 6074: ("JTL",  "variants"),
    6075: ("JTLW", "standard"), # Weekly Play JTL → remappe sur JTLW
    6023: ("JTL",  "multi"),    # J24 + J25 + P25 + P26 mélangés
    # LOF
    6105: ("LOF",  "standard"), 6188: ("LOF",  "variants"),
    6206: ("LOFW", "standard"), # Weekly Play LOF → remappe sur LOFW
    6418: ("LOF",  "multi"),    # P25 LOF-era
    # IBH
    6268: ("IBH",  "standard"),
    # P25
    6101: ("P25",  "standard"),
    # SEC
    6333: ("SEC",  "standard"), 6364: ("SEC",  "variants"),
    6386: ("SECW", "standard"), # Weekly Play SEC → remappe sur SECW
    # LAW
    6451: ("LAW",  "standard"), 6452: ("LAW",  "variants"),
    6453: ("LAWP", "standard"), # Weekly Play LAW → remappe sur LAWP
    # P26
    6472: ("P26",  "standard"),
}

# Priorité de set pour les expansions multi-sets (premier set prioritaire)
# 5626 = SOR special : C24 (Conv. Exclusive SOR) > GG > C25 > J25 > P25
# 6023 = JTL special : J24 > J25 > P25 > P26
# 6418 = LOF special2 : P25 > C25 > P26
MULTI_PRIORITY = {
    5626: ["C24", "C25", "GG", "J24", "J25", "P25"],
    6023: ["J24", "J25", "C24", "P25", "P26", "LOF", "SEC"],
    6418: ["P25", "C25", "P26"],
}

# ── Correspondance variant_type SWU → clé de prix ─────────────────────────────
_VT_TO_KEY = {
    # Sets principaux
    "Hyperspace":           "hyperspace",
    "Hyperspace Foil":      "hyperspace_foil",
    "Showcase":             "showcase",
    "Standard Prestige":    "standard_prestige",
    "Foil Prestige":        "foil_prestige",
    "Serialized Prestige":  "serialized_prestige",
    "Standard Foil":        "standard_foil",    # anciens sets
    # Weekly Play
    "Weekly Play":          "weekly_play",
    "Weekly Play Foil":     "weekly_play_foil",
    # Sets spéciaux
    "Convention Exclusive": "convention_exclusive",
    "Judge Program":        "judge_program",
    "GC VIP Promo":         "gc_vip_promo",
    "GC Event Pack":        "gc_event_pack",
    "GC Prize Wall":        "gc_prize_wall",
    "SQ Prize Wall":        "sq_prize_wall",
    "SQ Event Pack":        "sq_event_pack",
    "RQ Prize Wall":        "rq_prize_wall",
    "SS Participation":     "ss_participation",
    "SS Champion":          "ss_champion",
    "PQ Champion":          "pq_champion",
    "SQ Champion":          "sq_champion",
    "RQ Champion":          "rq_champion",
    "GC Top 64":            "gc_top64",
    "GC Top 8":             "gc_top8",
    "GC Champion":          "gc_champion",
    "Standard":             "standard",         # IBH / TS26
}

# Types traités par l'expansion "variants" CM pour les nouveaux sets
_STD_EXP_VT  = {"Standard", "Standard Foil"}
_VAR_EXP_VT  = {
    "Hyperspace", "Hyperspace Foil", "Showcase",
    "Standard Prestige", "Foil Prestige", "Serialized Prestige",
}

# Pour les anciens sets (SOR/SHD/TWI)
_OLD_VT_NONFOIL_ORDER  = ["Hyperspace", "Standard Prestige"]
_OLD_VT_FOIL_ORDER     = ["Showcase", "Hyperspace Foil", "Foil Prestige", "Serialized Prestige"]
_OLD_VT_FOIL_ORDER_LDR = ["Showcase", "Foil Prestige", "Serialized Prestige"]

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
    """'Capital City // Experience Token' → 'Capital City'
       'Chancellor Palpatine // Darth Sidious, Playing Both Sides' → 'Chancellor Palpatine, Playing Both Sides'
       'Battle Droid Token' → 'Battle Droid'  (strip Token suffix for token cards)"""
    if "//" not in cm_name:
        name = cm_name.strip()
        if name.endswith(" Token"):
            name = name[: -len(" Token")]
        return name
    left, right = [s.strip() for s in cm_name.split("//", 1)]
    if "," in right:
        subtitle = right.split(",", 1)[1].strip()
        return f"{left}, {subtitle}"
    return left

def price_entry(price_dict):
    if not price_dict:
        return {}
    foil_only = price_dict.get("avg") is None and price_dict.get("avg-foil") is not None
    if foil_only:
        # Variante foil-only (showcase, prestige, etc.) : tous les prix sont dans les champs foil CM
        return {
            "avg":         price_dict.get("avg-foil"),
            "low":         price_dict.get("low-foil"),
            "trend":       price_dict.get("trend-foil"),
            "avg_foil":    price_dict.get("avg-foil"),
            "low_foil":    price_dict.get("low-foil"),
            "trend_foil":  price_dict.get("trend-foil"),
            "avg1":        price_dict.get("avg1-foil"),
            "avg7":        price_dict.get("avg7-foil"),
            "avg30":       price_dict.get("avg30-foil"),
            "avg1_foil":   price_dict.get("avg1-foil"),
            "avg7_foil":   price_dict.get("avg7-foil"),
            "avg30_foil":  price_dict.get("avg30-foil"),
        }
    return {
        "avg":         price_dict.get("avg"),
        "low":         price_dict.get("low"),
        "trend":       price_dict.get("trend"),
        "avg_foil":    price_dict.get("avg-foil"),
        "low_foil":    price_dict.get("low-foil"),
        "trend_foil":  price_dict.get("trend-foil"),
        "avg1":        price_dict.get("avg1"),
        "avg7":        price_dict.get("avg7"),
        "avg30":       price_dict.get("avg30"),
        "avg1_foil":   price_dict.get("avg1-foil"),
        "avg7_foil":   price_dict.get("avg7-foil"),
        "avg30_foil":  price_dict.get("avg30-foil"),
    }

def foil_price_entry(price_dict):
    """Prix foil promus en champs primaires — pour Standard Foil / Hyperspace Foil des anciens sets.
    avg-foil peut être None (pas de avg CM) mais avg1-foil peut exister → fallback avg1."""
    avg_foil = (price_dict.get("avg-foil")
                or price_dict.get("avg1-foil")
                or price_dict.get("avg7-foil")
                or price_dict.get("avg30-foil"))
    return {
        "avg":         avg_foil,
        "low":         price_dict.get("low-foil"),
        "trend":       price_dict.get("trend-foil"),
        "avg_foil":    avg_foil,
        "low_foil":    price_dict.get("low-foil"),
        "trend_foil":  price_dict.get("trend-foil"),
        "avg1":        price_dict.get("avg1-foil"),
        "avg7":        price_dict.get("avg7-foil"),
        "avg30":       price_dict.get("avg30-foil"),
        "avg1_foil":   price_dict.get("avg1-foil"),
        "avg7_foil":   price_dict.get("avg7-foil"),
        "avg30_foil":  price_dict.get("avg30-foil"),
    }

def _has_foil_price(pd):
    """True si au moins un champ foil contient une valeur (avg-foil peut être None mais avg1-foil non)."""
    return any(pd.get(k) is not None for k in
               ["avg-foil", "low-foil", "trend-foil", "avg1-foil", "avg7-foil", "avg30-foil"])

def is_foil_only(pd):
    if pd.get("avg-foil") is not None:
        return pd.get("avg") is None
    return _has_foil_price(pd) and pd.get("avg") is None

def is_nonfoil_only(pd):
    return pd.get("avg") is not None and not _has_foil_price(pd)

def get_en_name(attrs):
    locs = attrs.get("localizations", {})
    loc_data = locs.get("data", []) if isinstance(locs, dict) else []
    for loc in loc_data:
        la = loc.get("attributes", {})
        if la.get("locale") == "en":
            title    = (la.get("title")    or "").strip()
            subtitle = (la.get("subtitle") or "").strip()
            return f"{title}, {subtitle}" if subtitle else title
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
def _build_swu_name_index():
    idx = {}
    for sc, fname in SET_FILES.items():
        path = f"{SETS_DIR}/{fname}"
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)
        cards = data.get("data", data) if isinstance(data, dict) else data
        idx[sc] = {normalize(get_en_name(c.get("attributes", c))) for c in cards}
    return idx

_swu_name_idx = _build_swu_name_index()

all_exp_ids = set(p["idExpansion"] for p in singles_data["products"])
new_exp_ids = all_exp_ids - set(EXPANSION_MAP_CONFIRMED.keys())

EXPANSION_MAP = dict(EXPANSION_MAP_CONFIRMED)

if new_exp_ids:
    print(f"\n  ⚠️  {len(new_exp_ids)} nouvelle(s) expansion(s) CM — auto-mapping :")
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
        known_for_set = [(eid, role) for eid, (sc, role) in EXPANSION_MAP.items() if sc == best_set]
        has_standard = any(r == "standard" for _, r in known_for_set)
        role = "variants" if has_standard else "standard"
        if best_score > 0:
            EXPANSION_MAP[exp_id] = (best_set, role)
            print(f"    idExpansion {exp_id:6d} ({n_prods:4d} prods) → {best_set} [{role}] ({best_score}/{len(cm_names)}) ← À CONFIRMER")
        else:
            print(f"    idExpansion {exp_id:6d} ({n_prods:4d} prods) → ??? (0 matchs — ignoré)")
else:
    print("  ✅ Aucune nouvelle expansion CM.")

# ── Chargement SWU ────────────────────────────────────────────────────────────
print("\nChargement sets SWU...")

# swu_cards[(set_code, norm_name)] = {
#   "en_name", "card_type", "standard_cn", "all_variants" [(cn,vt)…], "variant_types"
# }
swu_cards = {}

for set_code, fname in SET_FILES.items():
    path = f"{SETS_DIR}/{fname}"
    if not os.path.exists(path):
        continue
    with open(path) as f:
        data = json.load(f)
    cards        = data.get("data", data) if isinstance(data, dict) else data
    variants_lst = data.get("variants", []) if isinstance(data, dict) else []

    for card in list(cards) + list(variants_lst):
        attrs = card.get("attributes", card)
        en_name = get_en_name(attrs)
        norm    = normalize(en_name)
        cn      = attrs.get("cardNumber")

        vt_data      = attrs.get("variantTypes", {}).get("data", [])
        variant_type = vt_data[0]["attributes"]["name"] if vt_data else "Standard"

        card_type_obj = attrs.get("type", {})
        card_type = ""
        if isinstance(card_type_obj, dict):
            card_type = card_type_obj.get("data", {}).get("attributes", {}).get("name", "")

        key = (set_code, norm)
        if key not in swu_cards:
            swu_cards[key] = {
                "en_name":       en_name,
                "card_type":     card_type,
                "standard_cn":   None,
                "all_variants":  [],
                "variant_types": set(),
            }
        entry = swu_cards[key]
        entry["variant_types"].add(variant_type)
        if cn is not None:
            entry["all_variants"].append((cn, variant_type))
        if variant_type == "Standard" and cn is not None:
            entry["standard_cn"] = cn

total_cards = len(swu_cards)
print(f"  {total_cards} cartes uniques (nom × set)")

# Trier les variantes par numéro de carte
for key, entry in swu_cards.items():
    entry["all_variants"].sort(key=lambda x: (x[0] if x[0] is not None else 9999))
    # Fallback standard_cn : si pas de Standard, utiliser le numéro minimum
    if entry["standard_cn"] is None and entry["all_variants"]:
        entry["standard_cn"] = next(
            (cn for cn, _ in entry["all_variants"] if cn is not None), None
        )

# ── Index multi-sets pour les expansions "multi" ──────────────────────────────
# Construit un index global : norm_name → [(set_code, cn, vt), ...]
# Pour les sets impliqués dans les expansions "multi"
MULTI_SETS = set()
for priority_list in MULTI_PRIORITY.values():
    MULTI_SETS.update(priority_list)

multi_idx = defaultdict(list)  # norm_name → [(set_code, cn, vt), ...]
for (sc, norm), entry in swu_cards.items():
    if sc not in MULTI_SETS:
        continue
    for cn, vt in entry["all_variants"]:
        multi_idx[norm].append((sc, cn, vt))

# ── Groupement des produits CM par (set_code, norm_name) ─────────────────────
print("\nGroupement produits CM...")

cm_by_card = defaultdict(lambda: {"standard": [], "variants": [], "special": []})
unmapped_exp  = Counter()
multi_matched = Counter()   # (exp_id, set_code) → nb produits matchés
multi_orphans = []           # produits multi non matchés

for prod in singles_data["products"]:
    exp_id = prod["idExpansion"]
    if exp_id not in EXPANSION_MAP:
        unmapped_exp[exp_id] += 1
        continue

    set_code, role = EXPANSION_MAP[exp_id]

    if role == "ignored":
        continue

    card_name = cm_name_to_card_name(prod["name"])
    norm      = normalize(card_name)
    price     = cm_prices.get(prod["idProduct"], {})

    if role == "multi":
        # Chercher le set_code SWU par priorité
        priority = MULTI_PRIORITY.get(exp_id, [])
        candidates = multi_idx.get(norm, [])
        # Ensembles présents dans les candidats
        cand_sets = {c[0] for c in candidates}
        matched_sc = next((sc for sc in priority if sc in cand_sets), None)
        if matched_sc:
            cm_by_card[(matched_sc, norm)]["standard"].append(
                (prod["idProduct"], price, prod["name"])
            )
            multi_matched[(exp_id, matched_sc)] += 1
        else:
            multi_orphans.append((exp_id, prod["name"]))
        continue

    cm_by_card[(set_code, norm)][role].append((prod["idProduct"], price, prod["name"]))

if unmapped_exp:
    print(f"  ⚠️  Expansions non mappées: {dict(unmapped_exp)}")
if multi_matched:
    print("  Multi-sets matchés :")
    for (exp_id, sc), n in sorted(multi_matched.items()):
        print(f"    exp {exp_id} → {sc}: {n} produits")
if multi_orphans:
    print(f"  Multi-sets non matchés : {len(multi_orphans)} produits")
    if VERBOSE:
        for exp_id, name in multi_orphans[:10]:
            print(f"    exp={exp_id} '{name}'")

# ── Chargement overrides manuels ─────────────────────────────────────────────
MANUAL_PATH = f"{CM_DIR}/manual_mappings.json"
manual_overrides = {}   # (set_code, norm_name, price_key) → idProduct

if os.path.exists(MANUAL_PATH):
    with open(MANUAL_PATH) as f:
        _manual_raw = json.load(f)
    for m in _manual_raw:
        if not m.get("idProduct"):
            continue
        sc  = m["set_code"]
        n   = normalize(m["en_name"])
        pk  = _VT_TO_KEY.get(m["variant_type"], m["variant_type"].lower().replace(" ", "_"))
        manual_overrides[(sc, n, pk)] = m["idProduct"]
    if manual_overrides:
        print(f"  {len(manual_overrides)} override(s) manuel(s) chargé(s)")

# ── Construction de la table de prix ─────────────────────────────────────────
print("\nConstruction table de prix...")

price_table = []
unmatched   = []
mapping_log = []

for key, swu_info in swu_cards.items():
    set_code, norm  = key
    en_name         = swu_info["en_name"]
    standard_cn     = swu_info["standard_cn"]
    card_type       = swu_info["card_type"]
    variant_types   = swu_info["variant_types"]
    all_variants    = swu_info["all_variants"]
    cm_data         = cm_by_card.get(key, {"standard": [], "variants": [], "special": []})

    prices_out = {}
    is_old_set    = set_code in OLD_SETS
    is_weekly     = set_code in WEEKLY_PLAY_SETS
    is_special    = set_code in SPECIAL_PURE_SETS

    # ── Expansion Standard ──────────────────────────────────────────────────
    # Détermine les clés selon le type de set
    if is_weekly:
        std_key, foil_key = "weekly_play", "weekly_play_foil"
    elif is_special:
        # Pour les sets spéciaux purs : la clé est celle de leur unique variant type
        primary_vt = next((vt for _, vt in all_variants if vt != "Standard"), None)
        std_key  = _VT_TO_KEY.get(primary_vt, "standard") if primary_vt else "standard"
        foil_key = std_key  # pas de distinction foil/non-foil pour ces sets
    else:
        std_key, foil_key = "standard", "standard_foil"

    for idp, pr, _ in sorted(cm_data["standard"], key=lambda x: x[0]):
        if is_foil_only(pr):
            if foil_key not in prices_out:
                prices_out[foil_key] = {"idProduct": idp, **price_entry(pr)}
            # Anciens sets : un produit foil-only couvre quand même la variante non-foil
            if is_old_set and std_key not in prices_out:
                prices_out[std_key] = {"idProduct": idp, **price_entry(pr)}
        else:
            if std_key not in prices_out:
                prices_out[std_key] = {"idProduct": idp, **price_entry(pr)}
            # Anciens sets / multi-prods : avg-foil dans même produit (ou avg1-foil si avg-foil nul)
            if _has_foil_price(pr) and foil_key not in prices_out and foil_key != std_key:
                _pe = foil_price_entry if (is_old_set and card_type != "Leader") else price_entry
                prices_out[foil_key] = {"idProduct": idp, **_pe(pr)}

    # ── Expansion Variants ──────────────────────────────────────────────────
    if is_special or is_weekly:
        pass  # Ces sets n'ont pas d'expansion "variants" CM

    elif is_old_set:
        # Anciens sets (SOR/SHD/TWI) : split foil/non-foil
        all_sorted  = sorted(cm_data["variants"], key=lambda x: x[0])
        is_leader   = card_type == "Leader"
        foil_prods  = [(idp, pr) for idp, pr, _ in all_sorted if is_foil_only(pr)]
        nf_prods    = [(idp, pr) for idp, pr, _ in all_sorted if is_nonfoil_only(pr)]
        both_prods  = [(idp, pr) for idp, pr, _ in all_sorted
                       if not is_foil_only(pr) and not is_nonfoil_only(pr)]

        foil_order = _OLD_VT_FOIL_ORDER_LDR if is_leader else _OLD_VT_FOIL_ORDER
        nf_seq   = [_VT_TO_KEY[vt] for vt in _OLD_VT_NONFOIL_ORDER if vt in variant_types]
        foil_seq = [_VT_TO_KEY[vt] for vt in foil_order            if vt in variant_types]

        # Clés valides pour cette carte (évite d'assigner hyperspace_foil à un leader)
        valid_price_keys = {_VT_TO_KEY[vt] for vt in variant_types if vt in _VT_TO_KEY}
        _NF_TO_FOIL_KEY  = {"hyperspace": "hyperspace_foil", "standard_prestige": "foil_prestige"}

        # Cas normal : les produits sont différentiables par foil/non-foil
        for rank, (idp, pr) in enumerate(nf_prods + both_prods):
            if pr.get("avg") is None:
                continue
            if rank < len(nf_seq):
                k = nf_seq[rank]
                if k not in prices_out:
                    prices_out[k] = {"idProduct": idp, **price_entry(pr)}

        # Foil d'un produit "both" (ex: Hyperspace Foil = avg-foil du produit Hyperspace)
        for idp, pr in both_prods:
            foil_k = _NF_TO_FOIL_KEY.get(nf_seq[0]) if nf_seq else None
            if (foil_k and foil_k in valid_price_keys
                    and _has_foil_price(pr) and foil_k not in prices_out):
                _pe = foil_price_entry if not is_leader else price_entry
                prices_out[foil_k] = {"idProduct": idp, **_pe(pr)}

        if len(foil_prods) > len(foil_seq):
            for vt in foil_order:
                k = _VT_TO_KEY[vt]
                if k not in foil_seq:
                    foil_seq.append(k)
                if len(foil_seq) >= len(foil_prods):
                    break

        for rank, (idp, pr) in enumerate(foil_prods):
            if rank < len(foil_seq):
                k = foil_seq[rank]
                if k not in prices_out:
                    prices_out[k] = {"idProduct": idp, **price_entry(pr)}

        # Fallback positionnel : quand TOUS les produits sont foil-only et que nf_seq
        # n'a pas été rempli (ex : leader dont tous les produits variants sont foil-only).
        # On prend les premiers produits foil_prods non déjà assignés pour nf_seq.
        if nf_seq and all(k not in prices_out for k in nf_seq):
            assigned_ids = {v["idProduct"] for v in prices_out.values()}
            spare = [(idp, pr) for idp, pr in foil_prods if idp not in assigned_ids]
            for rank, k in enumerate(nf_seq):
                if rank < len(spare):
                    idp, pr = spare[rank]
                    if k not in prices_out:
                        prices_out[k] = {"idProduct": idp, **price_entry(pr)}
                    foil_k = _NF_TO_FOIL_KEY.get(k)
                    if (foil_k and foil_k in valid_price_keys
                            and _has_foil_price(pr) and foil_k not in prices_out):
                        prices_out[foil_k] = {"idProduct": idp, **foil_price_entry(pr)}

    else:
        # Nouveaux sets (JTL+) : matching positionnel
        variants_sorted = sorted(cm_data["variants"], key=lambda x: x[0])
        expected_variants = [
            (cn, vt) for cn, vt in all_variants if vt in _VAR_EXP_VT
        ]

        # Si plus de produits CM que de variantes SWU (ex : bases multi-tokens)
        # → favoriser les produits avec des données de prix
        if len(variants_sorted) > len(expected_variants):
            def _has_price(item):
                pr = item[1]
                return pr.get("avg") is not None or pr.get("avg-foil") is not None
            variants_for_match = sorted(
                variants_sorted, key=lambda x: (not _has_price(x), x[0])
            )
        else:
            variants_for_match = variants_sorted

        log_entries = []
        for rank, ((idp, pr, cm_name), (cn, vt)) in enumerate(
                zip(variants_for_match, expected_variants)):
            key_out = _VT_TO_KEY.get(vt)
            if key_out and key_out not in prices_out:
                prices_out[key_out] = {"idProduct": idp, "card_number": cn, **price_entry(pr)}
                if VERBOSE:
                    log_entries.append(
                        f"      [{rank}] #{cn:4d} {vt:20s} ← CM id={idp} '{cm_name[:35]}'"
                    )

        n_cm  = len(variants_sorted)
        n_swu = len(expected_variants)
        if n_cm != n_swu and (VERBOSE or abs(n_cm - n_swu) > 2):
            mapping_log.append(
                f"  ⚠️  {set_code} '{en_name}': {n_cm} produits CM vs {n_swu} variantes SWU"
            )
            if VERBOSE:
                mapping_log.extend(log_entries)

    # ── Assemblage ─────────────────────────────────────────────────────────
    if prices_out:
        price_table.append({
            "set_code":             set_code,
            "en_name":              en_name,
            "card_number":          standard_cn,
            "card_type":            card_type,
            "variant_types_in_app": sorted(variant_types),
            "prices":               prices_out,
        })
    else:
        unmatched.append({
            "set_code":      set_code,
            "en_name":       en_name,
            "card_number":   standard_cn,
            "variant_types": sorted(variant_types),
        })

# ── Application des overrides manuels ────────────────────────────────────────
if manual_overrides:
    # Index price_table par (set_code, norm_name) pour lookup rapide
    pt_idx = {(e["set_code"], normalize(e["en_name"])): e for e in price_table}
    applied = 0
    for (sc, n, pk), idp in manual_overrides.items():
        pr = cm_prices.get(idp, {})
        pe = {"idProduct": idp, **price_entry(pr)}
        if (sc, n) in pt_idx:
            if pk not in pt_idx[(sc, n)]["prices"]:
                pt_idx[(sc, n)]["prices"][pk] = pe
                applied += 1
        else:
            # Carte entièrement absente du price_table → la créer
            swu_key = (sc, n)
            if swu_key in swu_cards:
                info = swu_cards[swu_key]
                new_e = {
                    "set_code":             sc,
                    "en_name":              info["en_name"],
                    "card_number":          info["standard_cn"],
                    "card_type":            info["card_type"],
                    "variant_types_in_app": sorted(info["variant_types"]),
                    "prices":               {pk: pe},
                }
                price_table.append(new_e)
                pt_idx[(sc, n)] = new_e
                unmatched = [u for u in unmatched
                             if not (u["set_code"] == sc and normalize(u["en_name"]) == n)]
                applied += 1
    print(f"\n  ✅ {applied} override(s) manuel(s) appliqué(s)")

# ── Rapport ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("RÉSULTATS")
print(f"  Cartes avec prix     : {len(price_table)}")
print(f"  Cartes sans prix CM  : {len(unmatched)}")
print(f"  Couverture           : {len(price_table)/total_cards*100:.1f}%")

by_set_total   = Counter(k[0] for k in swu_cards.keys())
by_set_matched = Counter(e["set_code"] for e in price_table)
print("\n  Par set :")
for sc in sorted(by_set_total.keys()):
    total   = by_set_total[sc]
    matched = by_set_matched.get(sc, 0)
    bar = "█" * int(matched / total * 20) + "░" * (20 - int(matched / total * 20))
    print(f"    {sc:6s} {bar} {matched:4d}/{total:4d} ({matched/total*100:.0f}%)")

price_keys_count = Counter()
for e in price_table:
    for k in e["prices"]:
        price_keys_count[k] += 1
print("\n  Prix disponibles par type :")
for k, count in price_keys_count.most_common():
    print(f"    {k:25s}: {count:5d} cartes")

if unmatched:
    print(f"\n  Non matchées ({len(unmatched)}) :")
    tokens = [u for u in unmatched if u["en_name"] in ("Experience", "Shield", "Force", "Credit")]
    other  = [u for u in unmatched if u not in tokens]
    if tokens: print(f"    Tokens génériques : {len(tokens)} — normal")
    for u in other[:12]:
        print(f"    {u['set_code']:6s} #{str(u['card_number'] or '?'):4} {u['en_name'][:50]}")

if mapping_log:
    print(f"\n  Décalages CM ↔ SWU ({len(mapping_log)}) :")
    for line in mapping_log[:20]:
        print(line)

# Couverture variantes (nouveaux sets principaux)
print("\n  Couverture variantes (nouveaux sets) :")
check_sets = [sc for sc in by_set_total if sc not in OLD_SETS | WEEKLY_PLAY_SETS | SPECIAL_PURE_SETS
              and by_set_total[sc] > 5]
for sc in sorted(check_sets):
    expected = sum(
        len([vt for _, vt in e["all_variants"] if vt in _VAR_EXP_VT])
        for (s, _), e in swu_cards.items() if s == sc
    )
    produced = sum(
        len([k for k in e["prices"] if k not in ("standard", "standard_foil")])
        for e in price_table if e["set_code"] == sc
    )
    pct = produced / expected * 100 if expected else 0
    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    print(f"    {sc:6s} {bar} {produced:4d}/{expected:4d} ({pct:.0f}%)")

# ── Export prix courants ──────────────────────────────────────────────────────
price_date    = prices_data.get("createdAt", "")
snapshot_date = price_date[:10] if price_date else "unknown"

output = {
    "version":       2,
    "priceDate":     price_date,
    "expansion_map": {str(k): {"set_code": v[0], "role": v[1]} for k, v in EXPANSION_MAP.items()},
    "prices":        price_table,
}
os.makedirs(OUT_DIR, exist_ok=True)
out_path = f"{OUT_DIR}/cardmarket_prices.json"
with open(out_path, "w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n✅ {out_path} exporté ({len(price_table)} entrées)")

# ── Export historique ─────────────────────────────────────────────────────────
def compact_snapshot(entries):
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
if os.path.exists(history_path):
    with open(history_path) as f:
        history = json.load(f)
else:
    history = {"version": 1, "snapshots": []}

existing_dates = {s["date"] for s in history["snapshots"]}
if snapshot_date in existing_dates:
    print(f"  ℹ️  Snapshot {snapshot_date} déjà présent — mise à jour")
    history["snapshots"] = [s for s in history["snapshots"] if s["date"] != snapshot_date]

history["snapshots"].append({
    "date":   snapshot_date,
    "prices": compact_snapshot(price_table),
})
history["snapshots"].sort(key=lambda s: s["date"])

with open(history_path, "w") as f:
    json.dump(history, f, ensure_ascii=False, separators=(",", ":"))

nb = len(history["snapshots"])
dates = [s["date"] for s in history["snapshots"]]
print(f"✅ {history_path} mis à jour ({nb} snapshot(s) : {', '.join(dates)})")

# ── Mise à jour manual_mappings.json ─────────────────────────────────────────
# Calcule les variantes encore non couvertes après tout le matching
covered_now = set()
for e in price_table:
    n = normalize(e["en_name"])
    for pk in e["prices"]:
        covered_now.add((e["set_code"], n, pk))

# Construit la liste des entrées non couvertes (triées set/numéro de carte)
uncovered_entries = []
for (sc, norm_name), info in sorted(swu_cards.items(), key=lambda x: (x[0][0], x[0][1])):
    for cn, vt in info["all_variants"]:
        pk = _VT_TO_KEY.get(vt, vt.lower().replace(" ", "_"))
        if (sc, norm_name, pk) not in covered_now:
            uncovered_entries.append({
                "set_code":     sc,
                "en_name":      info["en_name"],
                "card_number":  cn,
                "variant_type": vt,
                "idProduct":    None,
                "note":         "",
            })
uncovered_entries.sort(key=lambda x: (x["set_code"], x["card_number"] or 0))

# Préserve les overrides manuels existants (idProduct renseigné)
existing_manual_map = {}
if os.path.exists(MANUAL_PATH):
    with open(MANUAL_PATH) as f:
        for m in json.load(f):
            if m.get("idProduct"):
                k = (m["set_code"], normalize(m["en_name"]),
                     _VT_TO_KEY.get(m["variant_type"], m["variant_type"].lower().replace(" ", "_")))
                existing_manual_map[k] = m

# Fusionne : entrées non couvertes + overrides préservés
manual_result = []
for entry in uncovered_entries:
    pk = _VT_TO_KEY.get(entry["variant_type"], entry["variant_type"].lower().replace(" ", "_"))
    k  = (entry["set_code"], normalize(entry["en_name"]), pk)
    manual_result.append(existing_manual_map.get(k, entry))

with open(MANUAL_PATH, "w") as f:
    json.dump(manual_result, f, ensure_ascii=False, indent=2)

n_filled = sum(1 for e in manual_result if e.get("idProduct"))
print(f"✅ {MANUAL_PATH} mis à jour ({len(manual_result)} entrées, {n_filled} ID renseigné(s))")
print("\nFin.")
