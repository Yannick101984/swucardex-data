# swucardex-data

Dépôt de données pour l'application **SWU Cardex** (iOS).
Contient les cartes Star Wars Unlimited, le manifest des sets, et les prix Cardmarket.

---

## Structure du repo

```
swucardex-data/
├── manifest.json                        # Manifest des sets (chargé au démarrage de l'app)
├── sets/                                # JSONs des cartes par set
│   ├── sor.json                         # Étincelle de Rébellion
│   ├── shd.json                         # Ombres de la Galaxie
│   ├── twi.json                         # Crépuscule de la République
│   ├── jtl.json                         # Passage en Vitesse Lumière
│   ├── lof.json                         # Légendes de la Force
│   ├── sec.json                         # Secrets du Pouvoir
│   ├── law.json                         # Sans Foi Ni Loi
│   └── ...                              # Sets spéciaux et promos
├── cardmarket/                          # Fichiers source Cardmarket (mis à jour manuellement)
│   ├── products_singles.json            # Catalogue des singles (nom, idExpansion, idMetacard)
│   └── price_guide.json                 # Prix actuels (avg, low, trend, foil…)
├── prices/                              # Générés automatiquement par GitHub Actions
│   ├── cardmarket_prices.json           # Prix actuels structurés par carte et variante
│   └── cardmarket_prices_history.json   # Historique accumulé des snapshots de prix
└── scripts/
    └── generate_prices.py               # Script de génération des fichiers de prix
```

---

## Ajouter un nouveau set

### 1. Préparer le JSON du set

Le fichier doit respecter le format `{"data": [...]}` avec la structure API officielle SWU.
Se référer au fichier `README - Format fichier JSON.txt` dans l'app Xcode pour le détail complet.

### 2. Ajouter le fichier dans `sets/`

```
sets/nom_du_set.json
```

Le code du set (ex: `LAW`) doit correspondre au champ `expansion.data.attributes.code` dans le JSON.

### 3. Mettre à jour `manifest.json`

Ajouter une entrée dans le tableau `sets` :

```json
{
  "id": "NEW",
  "name": "Nom français du set",
  "code": "NEW",
  "type": "main",
  "color": "#RRGGBB",
  "dataURL": "https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/sets/new.json",
  "logoURL": "...",
  "artworkURL": "...",
  "foilShared": false
}
```

### 4. Pousser

```bash
git add sets/new.json manifest.json
git commit -m "Ajout set NEW"
git push
```

L'app détecte automatiquement le nouveau set au prochain lancement (via le refresh du manifest).

---

## Mettre à jour les prix Cardmarket

Les prix sont fournis par [Cardmarket](https://www.cardmarket.com) via leurs fichiers d'export JSON publics.
Le suffixe `_21` dans les URLs est l'ID de catégorie SWU sur Cardmarket — il ne changera pas.

Tout est **automatique**, aucune manipulation manuelle requise :

### Ce que fait GitHub Actions

1. **`fetch_price_guide.yml`** (tous les jours à 00h01 UTC, ou déclenchement manuel) :
   télécharge `price_guide_21.json` → `cardmarket/price_guide.json` et
   `products_singles_21.json` → `cardmarket/products_singles.json`, puis commit/push si changement.
2. **`generate_prices.yml`** (déclenché automatiquement à la suite du workflow ci-dessus,
   ou à tout push modifiant `cardmarket/*.json`) :
   - Exécute `scripts/generate_prices.py`
   - Génère `prices/cardmarket_prices.json` — prix actuels par carte et variante
   - Met à jour `prices/cardmarket_prices_history.json` — ajoute un snapshot daté (les anciens snapshots sont conservés)
   - Commite et pousse les fichiers générés

Les deux workflows peuvent aussi être relancés manuellement depuis l'onglet **Actions** du repo sur GitHub.

### Ajouter un nouveau set aux prix Cardmarket

Rien à faire dans `scripts/generate_prices.py` : la liste des sets pris en charge
(`SET_FILES`, `WEEKLY_PLAY_SETS`, `SPECIAL_PURE_SETS`) est dérivée automatiquement de
`manifest.json` à chaque exécution. Il suffit d'avoir suivi la procédure
["Ajouter un nouveau set"](#ajouter-un-nouveau-set) ci-dessus (fichier dans `sets/` + entrée
dans `manifest.json`) pour que le set soit pris en compte au prochain run.

Seul le mapping `EXPANSION_MAP_CONFIRMED` (idExpansion Cardmarket → set) reste manuel : au
premier run suivant l'apparition d'un nouveau set chez Cardmarket, le script tente un
auto-mapping par similarité de noms et l'affiche dans les logs avec `⚠️ À CONFIRMER` — il est
recommandé de le reporter ensuite en dur dans `EXPANSION_MAP_CONFIRMED` pour fiabiliser les runs
suivants.

### Historique des prix

Chaque push ajoute un snapshot daté dans `cardmarket_prices_history.json` :

```json
{
  "snapshots": [
    { "date": "2026-04-12", "prices": { "SOR|Director Krennic, Aspiring to Authority": { "standard": 0.08 } } },
    { "date": "2026-05-15", "prices": { ... } }
  ]
}
```

L'app utilise cet historique pour afficher les graphes d'évolution des prix dans la fiche de chaque carte.

---

## Couverture des prix

| Sets | Couverture |
|---|---|
| SOR, SHD, TWI, JTL, LOF, SEC, LAW | ~100% (Standard, Hyperspace, Foil, Prestige, Showcase) |
| IBH, P25, P26 | 96–100% |
| C24, C25, GG, J24, J25, TS26 | 0% — non disponibles sur Cardmarket |
| JTLP, LOFP, SECP, LAWP, ASH, ASHP | à revérifier après la correction du #98 (codes W→P) et du bug ASH — voir historique du repo |

Les tokens (Experience, Shield, etc.) ne sont pas vendus séparément sur Cardmarket — normal.
Les variantes **Prestige** et **Serialized Prestige** sont couvertes à partir de JTL.

---

## URLs utilisées par l'app

```
Manifest    : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/manifest.json
Set JSON    : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/sets/{id}.json
Prix actuels: https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/prices/cardmarket_prices.json
Historique  : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/prices/cardmarket_prices_history.json
```
