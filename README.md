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

Les prix sont fournis par [Cardmarket](https://www.cardmarket.com) via leurs fichiers d'export JSON.
Cardmarket ne fournit plus d'API directe — les fichiers sont disponibles dans l'espace développeur du compte.

### Fichiers à télécharger depuis Cardmarket

| Fichier Cardmarket | À renommer en | Destination |
|---|---|---|
| `products_singles_21.json` | `products_singles.json` | `cardmarket/` |
| `price_guide_21.json` (le plus récent) | `price_guide.json` | `cardmarket/` |

> **Note :** le suffixe `_21` est l'ID de catégorie SWU sur Cardmarket — il ne changera pas.
> Si Cardmarket publie plusieurs versions du price guide le même jour, prendre le fichier le plus récent.

### Procédure de mise à jour

```bash
# Copier les fichiers renommés dans le repo
cp ~/Desktop/products_singles.json cardmarket/
cp ~/Desktop/price_guide.json cardmarket/

git add cardmarket/
git commit -m "Mise à jour prix Cardmarket [YYYY-MM-DD]"
git push
```

**C'est tout.** GitHub Actions se charge du reste automatiquement.

### Ce que fait GitHub Actions

Déclenchement automatique à chaque push modifiant `cardmarket/products_singles.json` ou `cardmarket/price_guide.json`.

1. Exécute `scripts/generate_prices.py`
2. Génère `prices/cardmarket_prices.json` — prix actuels par carte et variante
3. Met à jour `prices/cardmarket_prices_history.json` — ajoute un snapshot daté (les anciens snapshots sont conservés)
4. Commite et pousse les deux fichiers générés

Le workflow peut aussi être relancé manuellement depuis l'onglet **Actions** du repo sur GitHub.

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
| C24, C25, GG, J24, J25, JTLW, LOFW, SECW, TS26 | 0% — non disponibles sur Cardmarket |

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
