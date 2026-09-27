# Control-M JSON → Mermaid

Outil de génération de **graphes Mermaid à partir d'exports JSON Control-M**, avec prise en charge des dépendances entre traitements, des vues globales et SubFolder, ainsi que de la génération automatisée des formats `.mmd`, `.svg` et `.png` via Azure DevOps.

---

## Présentation

Le projet permet de transformer un export JSON Control-M en représentation graphique Mermaid afin de faciliter :

- la compréhension d'une chaîne Control-M ;
- l'analyse des dépendances entre Jobs et SubFolders ;
- la documentation des traitements et de leurs relations ;
- la visualisation de structures Control-M de tailles différentes ;
- la production de vues globales ou détaillées selon le besoin.

Le convertisseur Python analyse directement le JSON Control-M et construit les diagrammes Mermaid à partir de la structure et des Events présents dans l'export.

---

## Arborescence du projet

```text
README.md
cipipeline/
└── ctm_to_mermaid/
    ├── ctm_json_to_mermaid.py
    └── ctm_json_to_mermaid.yml
```

Le pipeline Azure DevOps attend le script Python à l'emplacement suivant :

```text
cipipeline/ctm_to_mermaid/ctm_json_to_mermaid.py
```

---

# Fonctionnement du convertisseur

## Structure Control-M

Le convertisseur analyse les objets Control-M présents dans le JSON :

```text
Folder
└── SubFolder
    ├── Job
    ├── Job
    └── ...
```

Il prend en charge deux structures d'export JSON :

1. les objets nommés directement par leur clé JSON ;
2. les objets contenus dans une liste `Jobs`, avec leur nom stocké dans l'attribut `Name`.

Lorsque plusieurs occurrences possèdent le même nom dans une liste `Jobs`, elles restent distinctes dans le modèle interne afin d'éviter les collisions d'identifiants Mermaid.

Le nom Control-M affiché dans le graphe reste inchangé.

---

## Dépendances Control-M

Les dépendances sont reconstruites à partir des **Events Control-M**.

Principe :

```text
Producteur d'un Event
        +
Consommateur du même Event
        =
Dépendance source → cible
```

Le convertisseur ne déduit jamais une dépendance à partir du nom d'un Job ou d'un SubFolder.

La portée réelle de l'Event est conservée :

- un `Event:Add` conditionnel présent dans un Job reste rattaché à ce Job ;
- un `eventsToAdd` défini directement sur un SubFolder reste rattaché à ce SubFolder.

Un Job et son SubFolder parent peuvent donc produire le même Event et générer deux liens distincts vers une même cible.

---

## Couleurs des dépendances

Les liens sont différenciés selon le type de production de l'Event :

| Couleur | Signification |
|---|---|
| Vert | production `OK` uniquement |
| Rouge | production `NOTOK` uniquement |
| Orange | même relation présente en `OK` et en `NOTOK` |

Lorsque le mode debug est activé, le nom des Events/conditions est affiché directement sur les flèches.

---

## Informations affichées

Selon les informations présentes dans le JSON, les graphes peuvent afficher notamment :

- le nom du Folder ;
- le nom du SubFolder ;
- le nom du Job ;
- le Host ;
- le compte d'exécution ;
- la commande ou le script ;
- la planification ;
- l'information de cyclicité.

Les titres Folder/SubFolder regroupent les principales informations de planification sur une seule ligne afin de conserver un rendu compact.

---

# Modes de génération Mermaid

Le paramètre `generationMode` permet de choisir les graphes Mermaid produits.

## `global`

Génère un **graphe Mermaid global** représentant l'ensemble de la structure présente dans le JSON.

Exemple :

```text
MON_EXPORT.mmd
```

## `subfolders`

Génère un **graphe Mermaid indépendant pour chaque SubFolder**.

Exemple :

```text
MON_EXPORT_SUBFOLDER_xxx.mmd
MON_EXPORT_SUBFOLDER_yyy.mmd
```

Ce mode est particulièrement adapté aux chaînes importantes, lorsque le graphe global devient trop volumineux.

## `both`

Génère :

- le graphe Mermaid global ;
- les graphes Mermaid individuels de chaque SubFolder.

C'est le mode par défaut.

---

# Layout Mermaid

## `vertical`

Mode par défaut.

Il favorise une lecture **de haut en bas**.

Des contraintes graphiques transparentes peuvent être ajoutées entre certains composants indépendants afin d'améliorer leur placement.

Ces contraintes sont uniquement utilisées pour la mise en page et ne représentent aucune dépendance Control-M.

## `auto`

Laisse Mermaid choisir librement le placement des éléments.

---

# Renderer Mermaid

Deux renderers sont disponibles :

```text
dagre-d3
dagre-wrapper
```

`dagre-d3` est utilisé par défaut.

Le renderer sélectionné est inscrit directement dans le fichier `.mmd`.

---

# Espacement du graphe

## `rankSpacing`

Espacement vertical entre les rangs Mermaid.

Valeur par défaut :

```text
30 px
```

Valeurs acceptées par le script :

```text
10 à 200 px
```

## `nodeSpacing`

Espacement entre les nœuds placés sur un même rang.

Valeur par défaut :

```text
45 px
```

Valeurs acceptées par le script :

```text
10 à 200 px
```

---

# Utilisation locale du script Python

## Prérequis

- Python 3.x
- aucune dépendance Python externe requise

Afficher la version :

```bash
python ctm_json_to_mermaid.py --version
```

Exemple simple :

```bash
python ctm_json_to_mermaid.py MON_EXPORT.json
```

Exemple complet :

```bash
python ctm_json_to_mermaid.py MON_EXPORT.json \
    --show-events false \
    --mode both \
    --layout vertical \
    --renderer dagre-d3 \
    --rank-spacing 30 \
    --node-spacing 45 \
    --output-dir ./mermaid-output
```

## Options CLI

| Option | Valeurs | Défaut | Description |
|---|---|---:|---|
| `json_file` | chemin JSON | - | Fichier Control-M à analyser |
| `--show-events` | `true` / `false` | `false` | Affiche les Events sur les flèches |
| `--mode` | `global`, `subfolders`, `both` | `both` | Type de génération |
| `--output-dir` | chemin | dossier du JSON | Répertoire de sortie |
| `--layout` | `vertical`, `auto` | `vertical` | Mode de placement |
| `--renderer` | `dagre-d3`, `dagre-wrapper` | `dagre-d3` | Renderer Mermaid |
| `--rank-spacing` | 10 à 200 | 30 | Espacement vertical |
| `--node-spacing` | 10 à 200 | 45 | Espacement horizontal |
| `--version` | - | - | Affiche la version du script |

Sans argument, le script peut également fonctionner en mode interactif.

---

# Pipeline Azure DevOps

Le pipeline est défini dans :

```text
cipipeline/ctm_to_mermaid/ctm_json_to_mermaid.yml
```

Dans Azure DevOps, il est destiné à être utilisé sous :

```text
CONTROL-M
└── Pipelines
    └── MERMAID
        └── CTM_JSON_TO_MERMAID
```

La pipeline est volontairement manuelle :

```yaml
trigger: none
pr: none
```

Elle se lance donc depuis **Run pipeline**.

Le YAML vérifie actuellement que l'exécution Azure DevOps est effectuée depuis la branche :

```text
integration
```

Cette contrainte concerne uniquement l'exécution de la pipeline Azure DevOps.

Le dépôt GitHub du projet reste publié sur sa branche principale :

```text
main
```

---

# Sources JSON Control-M

Le paramètre `targetSource` permet de choisir à la fois :

- le type de source ;
- l'environnement.

Deux familles de sources sont disponibles.

---

## BUILD

Les sources **BUILD** correspondent aux exports JSON stockés dans les répertoires de build par environnement.

| Source | Répertoire |
|---|---|
| `DEV BUILD` | `build/dev` |
| `PPRD BUILD` | `build/pep` |
| `QAL BUILD` | `build/qaf` |
| `PROD BUILD` | `build/prd` |

Ces répertoires peuvent contenir les JSON correspondant à une chaîne ou à un ensemble de chaînes Control-M.

Exemple :

```text
MON_EXPORT.json
```

---

## SMARTFOLDER

Les sources **SMARTFOLDER** correspondent aux exports JSON de SmartFolders stockés par environnement.

| Source | Répertoire |
|---|---|
| `DEV SMARTFOLDER` | `src/smartfolder/dev` |
| `PPRD SMARTFOLDER` | `src/smartfolder/pep` |
| `QAL SMARTFOLDER` | `src/smartfolder/qaf` |
| `PROD SMARTFOLDER` | `src/smartfolder/prd` |

Ces répertoires contiennent les SmartFolders extraits automatiquement depuis Control-M et régulièrement mis à jour.

Pour les structures volumineuses, il peut être préférable de sélectionner directement le SmartFolder concerné afin d'éviter de travailler sur un export global trop important.

Il faut bien distinguer :

```text
SMARTFOLDER
```

qui correspond à la **source du JSON**, et :

```text
global / subfolders / both
```

qui correspond au **mode de génération des graphes Mermaid**.

---

# Paramètres du pipeline

## `targetSource`

Valeurs disponibles :

```text
DEV BUILD
PPRD BUILD
QAL BUILD
PROD BUILD

DEV SMARTFOLDER
PPRD SMARTFOLDER
QAL SMARTFOLDER
PROD SMARTFOLDER
```

Défaut :

```text
DEV BUILD
```

---

## `jsonFile`

Nom du fichier JSON Control-M à traiter.

Exemple :

```text
MON_EXPORT.json
```

Le chemin ne doit pas être indiqué.

Le pipeline refuse notamment :

- `/` ;
- `\` ;
- `..` ;
- un fichier sans extension `.json`.

Le répertoire utilisé est automatiquement déterminé à partir de `targetSource`.

---

## `debugConditions`

Défaut :

```text
false
```

- `false` : masque le nom des Events sur les flèches ;
- `true` : affiche les Events/conditions pour faciliter le diagnostic.

---

## `generationMode`

Valeurs :

```text
global
subfolders
both
```

Défaut :

```text
both
```

---

## `layoutMode`

Valeurs :

```text
vertical
auto
```

Défaut :

```text
vertical
```

---

## `rendererMode`

Valeurs :

```text
dagre-d3
dagre-wrapper
```

Défaut :

```text
dagre-d3
```

---

## `rankSpacing`

Défaut :

```text
30
```

---

## `nodeSpacing`

Défaut :

```text
45
```

---

## `pngViewportHeight`

Défaut :

```text
1200
```

Valeurs autorisées :

```text
200 à 10000 px
```

Ce paramètre :

- ne limite pas la hauteur finale du graphe ;
- ne redimensionne pas le SVG ;
- ne modifie pas la taille des textes ;
- limite uniquement la hauteur du viewport Chromium utilisée pendant la rasterisation.

La capture `fullPage` permet ensuite de récupérer la totalité du document.

---

## `renderSvg`

Défaut :

```text
true
```

- `true` : conserve les SVG dans l'artifact ;
- `false` : ne conserve pas les SVG.

Si `renderSvg=false` et `renderPng=true`, un SVG intermédiaire est généré pour produire le PNG puis supprimé.

---

## `renderPng`

Défaut :

```text
true
```

- `true` : génère les PNG ;
- `false` : ne génère pas de PNG.

---

# Chaîne de rendu

Le pipeline utilise la chaîne suivante :

```text
JSON Control-M
      ↓
Python
      ↓
MMD
      ↓
Mermaid CLI
      ↓
SVG
      ↓
Chromium / Puppeteer
      ↓
PNG
```

Mermaid n'est exécuté qu'une seule fois par diagramme.

Le PNG est rasterisé à partir du SVG déjà généré, ce qui permet de conserver le même layout entre les deux formats.

La largeur Chromium est adaptée à la largeur réelle du SVG afin d'éviter la troncature horizontale des gros graphes.

La capture `fullPage` permet de gérer les diagrammes très hauts.

Le PNG est généré avec :

```text
deviceScaleFactor = 2
```

pour améliorer la netteté sans modifier les proportions du diagramme.

---

# Mermaid CLI

Le pipeline utilise l'image Docker suivante :

```text
ghcr.io/mermaid-js/mermaid-cli/mermaid-cli:11.16.1
```

La version est volontairement figée afin de garantir un rendu reproductible.

Chromium/Puppeteer est utilisé pour la conversion SVG → PNG, notamment afin de conserver correctement les labels HTML et les `foreignObject` produits par Mermaid.

---

# Artifact Azure DevOps

Le pipeline publie l'artifact :

```text
controlm-mermaid-diagrams
```

Selon les options choisies, il contient :

```text
*.mmd
*.svg
*.png
generation-info.txt
```

## `.mmd`

Source Mermaid du diagramme.

## `.svg`

Version vectorielle du graphe.

À privilégier pour :

- les gros diagrammes ;
- le zoom ;
- la conservation d'une qualité maximale.

## `.png`

Version image directement exploitable dans :

- un document ;
- un mail ;
- une documentation technique ;
- tout support acceptant un format image standard.

## `generation-info.txt`

Fichier de traçabilité contenant notamment :

- le numéro de build Azure DevOps ;
- le Build ID ;
- le commit ;
- la source utilisée ;
- le dossier du repository ;
- le JSON traité ;
- les paramètres de génération ;
- le renderer ;
- les espacements ;
- les options SVG/PNG ;
- le viewport PNG ;
- la version Mermaid CLI.

---

# Traçabilité du convertisseur

Lors d'un run Azure DevOps, la pipeline affiche également :

```bash
python ctm_json_to_mermaid.py --version
sha256sum ctm_json_to_mermaid.py
```

Cela permet d'identifier précisément la version du script utilisée pour générer un artifact.

---

# Recommandations d'utilisation

Pour une structure de taille réduite ou moyenne :

```text
Mode : global ou both
```

Pour une structure volumineuse :

```text
Mode : subfolders ou both
```

Le mode `both` permet de conserver à la fois une vue d'ensemble et des vues détaillées par SubFolder.

Le mode debug peut être activé si nécessaire :

```text
debugConditions = true
```

Il affiche les noms des Events/conditions sur les flèches et facilite l'analyse des dépendances.

Le format SVG est recommandé lorsqu'un diagramme nécessite un zoom important ou une conservation maximale de la qualité.

---

# Diagnostic

En cas d'anomalie, conserver si possible :

```text
JSON source
MMD
SVG
PNG
generation-info.txt
```

Exemples de problèmes à remonter :

- Job manquant ;
- SubFolder manquant ;
- dépendance absente ;
- dépendance incorrecte ;
- mauvaise couleur de lien ;
- Event rattaché au mauvais producteur ;
- problème de layout ;
- texte tronqué ;
- PNG incomplet ;
- différence inattendue entre SVG et PNG.

Pour analyser une dépendance, relancer si nécessaire avec :

```text
debugConditions = true
```

---

# Principes de maintenance

Les règles suivantes doivent rester respectées lors des évolutions du convertisseur :

1. les Events Control-M restent la source de vérité des dépendances ;
2. l'ordre des traitements n'est jamais déduit de leur nom ;
3. un Event conditionnel reste rattaché à son producteur réel ;
4. les contraintes de layout restent séparées des dépendances métier ;
5. les sorties SVG et PNG doivent conserver le même layout ;
6. une amélioration de mise en page ne doit jamais créer une fausse dépendance Control-M.

---

# GitHub

Le dépôt GitHub est publié sur :

```text
main
```

Pour une modification classique :

```bash
git status
git add .
git commit -m "Description de la modification"
git push
```

La branche `integration` mentionnée dans le YAML concerne l'exécution du pipeline **Azure DevOps** et ne modifie pas le fonctionnement du dépôt GitHub.

---

# Fichiers principaux

```text
README.md
cipipeline/
└── ctm_to_mermaid/
    ├── ctm_json_to_mermaid.py
    └── ctm_json_to_mermaid.yml
```

- `ctm_json_to_mermaid.py` : logique de conversion Control-M JSON → Mermaid ;
- `ctm_json_to_mermaid.yml` : orchestration Azure DevOps, rendu SVG/PNG et publication de l'artifact ;
- `README.md` : documentation du projet.
