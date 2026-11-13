# swucardex-data

Dépôt de données pour l'application **SWU Cardex** (iOS).
Contient les cartes Star Wars Unlimited, le manifest des sets, et les prix Cardmarket.

---

## Structure du repo

```
swucardex-data/
├── manifest.json                        # Manifest des sets (chargé au démarrage de l'app)
├── rotation.json                        # Rotation Premier, tenue à la main (voir plus bas)
├── prebuilt_decks.json                  # Decks préconstruits officiels, tenus à la main (voir plus bas)
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

## Stratégie de branches : `main` vs `staging`

**`main`** est l'unique source de vérité pour la production (App Store) — c'est la branche
que lisent les builds Release de l'app. **`staging`** est utilisée par les builds Debug
(TestFlight interne / device de dev) pour tester des changements de **code** (scripts,
workflows) avant de les valider sur `main`.

Tous les workflows qui génèrent de la donnée tournent en matrice `[main, staging]` et
poussent indépendamment sur les deux branches (`fetch_news.yml`, `fetch_gamegenic.yml`,
`fetch_price_guide.yml` + `generate_prices.yml`, `swu_sync_sets.yml`, `swu_compare_sets.yml`) :
chaque branche exécute le même script contre la même source externe et commite son propre
résultat. `staging` reste donc à jour automatiquement sans jamais dépendre d'une promotion —
et le code (scripts/workflows) reste librement testable sur `staging` sans qu'un cron nocturne
ne l'écrase, puisque seuls les fichiers de données sont touchés par ces jobs.

Point d'attention pour les crons déclenchés par `schedule` : la variable `GITHUB_REF_NAME`
vaut toujours la branche par défaut (`main`), même quand le job checkout `staging` via
`ref: ${{ matrix.branch }}`. Le step qui appelle `scripts/sync_sets.py` force donc
`GITHUB_REF_NAME` à `${{ matrix.branch }}` dans son `env:` pour que les `dataURL` générés dans
`manifest.json` référencent la bonne branche sur chaque leg de la matrice.

**`promote_staging.yml`** (déclenchement manuel, publication TestFlight/App Store) reste le
mécanisme pour faire passer un changement de **code** de `staging` vers `main` : il fusionne
`staging` → `main` avec `-X theirs` (le code de `staging` gagne), puis **restaure
explicitement la version de `main`** pour tous les fichiers de données. Ça évite de rejouer
l'incident du 2026-07-23 (86 jours d'historique de prix effacés par une version figée de
`staging` — voir swucardex-data#5) dans le cas où les deux branches auraient malgré tout
diverger sur la donnée (panne d'un cron, ajout manuel, etc.).

Si `staging` dérive quand même de `main` (contenu différent en dehors d'un test de code en
cours), le resynchroniser entièrement plutôt que de merger :

```bash
git push origin main:refs/heads/staging --force
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

### 5. Classer le set dans `rotation.json`

Voir « Rotation Premier » ci-dessous. Tant que le set n'y figure pas, il ne compte pas dans la rotation.

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

## Annonces in-app

| Fichier | Lu par | Contenu |
|---|---|---|
| `announcements.json` | iOS | annonces communes + annonces de mise à jour iOS (lien App Store) |
| `announcements-android.json` | Android | annonces communes + annonces de mise à jour Android (lien Play Store) |

- **Annonce de MAJ iOS** → `announcements.json` uniquement.
- **Annonce de MAJ Android** → `announcements-android.json` uniquement.
- **Annonce commune** (nouvelles cartes, règles, sets, soutien…) → les **deux** fichiers, avec le **même `id`**.
  `scripts/sync_sets.py` le fait automatiquement pour les annonces de cartes et de règles.

Même schéma pour les deux fichiers : tableau d'objets `id`, `title`, `detail`, `color`, `url`, `imageURL`, `date`, `changes`, le plus récent en premier.

---

## Rotation Premier (`rotation.json`)

La légalité en Premier ne se déduit pas des données récupérées automatiquement. L'API officielle ne donne ni date de sortie, ni marque de rotation, ni distinction entre set principal et mini-set (cas d'IC27). Ce fichier est donc **tenu à la main**. `sync_sets.py` ne le modifie jamais : il ouvre une issue « Rotation : classer … » quand il ajoute un set absent du fichier.

```json
{
  "premier": {
    "blocks": [
      { "mark": "A", "sets": ["JTL", "LOF", "IBH", "SEC"], "legalUntil": null }
    ],
    "legalFrom": { "IC27": "2026-11-20" }
  }
}
```

- `blocks` : un bloc par marque de rotation (A, B, C…). Tous les sets d'un bloc sortent de rotation ensemble, à la date `legalUntil`. Cette date est exclue (le jour même, le bloc n'est plus légal). `null` veut dire pas encore annoncée.
- `legalFrom` : date de sortie officielle d'un set, le jour même inclus. Elle sert surtout pour un set publié en avant-première. Si le set n'y figure pas, il est légal dès qu'il apparaît dans un bloc.
- Une carte est légale si au moins une de ses impressions est dans un set légal. Les réimpressions, promos et Weekly Play suivent donc la carte d'origine.
- Un set absent du fichier ne décide jamais de la légalité. Promos, Weekly Play et conventions n'ont pas besoin d'y figurer.
- Dates au format `AAAA-MM-JJ`. Il faut mettre à jour `lastUpdated` à chaque modification.

**À l'annonce d'un nouveau set principal** : l'ajouter au bloc de sa marque avec sa date de sortie dans `legalFrom`. Si ce set ouvre un nouveau bloc, il faut aussi renseigner `legalUntil` sur le bloc qui sort de rotation.

---

## Decks préconstruits (`prebuilt_decks.json`)

Listes des decks préconstruits officiels (starters deux joueurs, Spotlight Decks, Bataille d'intro, Twin Suns), affichées dans l'onglet « Préconstruits » du Deck Manager (#232). Ce fichier est **tenu à la main** : l'API officielle ne décrit pas le contenu des produits.

```json
{
  "version": 1,
  "lastUpdated": "2026-10-29",
  "products": [
    {
      "id": "law-spotlight", "name": "Sans Foi Ni Loi — Spotlight", "set": "LAW", "format": "premier",
      "decks": [
        {
          "id": "law-leia", "name": "Leia Organa",
          "leader": { "id": "LAW_010", "count": 1 },
          "base": { "id": "LAW_020", "count": 1 },
          "deck": [{ "id": "LAW_037", "count": 3 }],
          "sideboard": [],
          "sources": ["Liste officielle FFG (image de l'article starwarsunlimited.com)"]
        }
      ]
    }
  ]
}
```

- `products` : un produit par boîte vendue, du plus récent au plus ancien (ordre d'affichage). `format` vaut `premier` ou `twinSuns`. L'app passe d'elle-même un deck en Éternel quand une de ses cartes est sortie de rotation (`rotation.json`).
- Les decks utilisent le format JSON de SWUDB : `leader`, `secondleader` (Twin Suns uniquement), `base`, `deck` et `sideboard`, avec des identifiants `SET_NNN` (le numéro de nos `sets/*.json`, sur 3 chiffres).
- Une carte réimprimée peut garder l'identifiant de son set d'origine. Une même carte sous plusieurs numéros (IBH : un numéro par exemplaire) n'a qu'une entrée, avec le total des exemplaires.
- Un deck fait 50 cartes (80 en Twin Suns), leader(s) et base non compris. Il faut au plus 3 exemplaires par carte (1 en Twin Suns).
- `sources` indique d'où vient la liste. Les images officielles des articles starwarsunlimited.com font foi. Attention, sur ces images, les cartes JTL marquées * ont un numéro supérieur de 2 à celui de nos données : il faut les retrouver par leur nom.
- Il faut mettre à jour `lastUpdated` à chaque modification : l'app garde la version la plus récente entre son cache et le fichier fourni avec l'app.

---

## URLs utilisées par l'app

```
Manifest    : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/manifest.json
Rotation    : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/rotation.json
Préconstruits: https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/prebuilt_decks.json
Set JSON    : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/sets/{id}.json
Prix actuels: https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/prices/cardmarket_prices.json
Historique  : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/prices/cardmarket_prices_history.json
Annonces iOS     : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/announcements.json
Annonces Android : https://raw.githubusercontent.com/Yannick101984/swucardex-data/main/announcements-android.json
```
