"""
Convertisseur Control-M JSON -> Mermaid.

Objectif
--------
Lire un export JSON Control-M et produire des diagrammes Mermaid (.mmd)
exploitables localement ou depuis Azure DevOps.

Le script peut générer :
- le diagramme global du Folder ;
- un diagramme séparé pour chaque SubFolder ;
- les dépendances déduites des Events Control-M ;
- les Events orphelins dans la vue globale ;
- l'information de planification et de cyclicité lorsqu'elle est disponible.

Formats JSON pris en charge
---------------------------
Deux structures d'export Control-M sont gérées :

1. Format "key" :
   les objets sont nommés par leur clé JSON.

2. Format "list" :
   les objets sont contenus dans une liste "Jobs" et leur nom est porté par
   l'attribut "Name".

Si plusieurs occurrences possèdent le même Name dans une liste Jobs, elles
restent distinctes. Le nom Control-M affiché ne change pas ; seul le path/ID
technique reçoit un suffixe __OCC_n.

Dépendances Control-M
---------------------
La source de vérité est l'Event Control-M :
- un objet qui produit un Event est un producteur ;
- un objet qui attend ce même Event est un consommateur ;
- producteur + consommateur => dépendance source -> cible.

Aucune dépendance n'est déduite du nom des jobs ou des SubFolders.

Portée du producteur
--------------------
La dépendance reste attachée à l'objet qui porte réellement l'Event :
- Event:Add conditionnel dans un Job => producteur = ce Job ;
- eventsToAdd porté directement par un SubFolder => producteur = ce SubFolder.

Le script ne remonte donc jamais artificiellement un Event de Job vers son
SubFolder parent. Un Job et son SubFolder peuvent produire le même Event et
générer deux liens distincts vers la même cible.

Couleurs des dépendances
------------------------
- vert   : production OK uniquement ;
- rouge  : production NOTOK uniquement ;
- orange : le même couple source -> cible existe en OK et en NOTOK.

Lorsque --show-events=true, les noms des Events sont affichés sur les flèches.

Layout Mermaid
--------------
Le mode "vertical" demande un flux haut -> bas et ajoute seulement des liens
transparents entre composantes indépendantes de SubFolders frères. Ces liens
servent exclusivement au placement Mermaid et ne représentent aucune
dépendance Control-M.

Folder et SubFolder sont reliés directement par leur ID de subgraph. Aucun nœud
technique d'ancrage n'est ajouté.

Titres Folder/SubFolder
-----------------------
Le nom, la planification et l'éventuelle information cyclique sont regroupés sur
une seule ligne dans le titre natif du subgraph.

Exemple :
    📁 FOLDER_A · Planif: CAL:EVERYDAY · 🔁 Cyclique: toutes les 5 min

Ce choix garde le titre centré par Mermaid et évite d'ajouter des rangs
techniques uniquement pour afficher la planification.

Espacement
----------
Les paramètres suivants permettent d'ajuster le rendu sans modifier le code :
- --rank-spacing : espacement vertical entre rangs, 30 px par défaut ;
- --node-spacing : espacement entre nœuds d'un même rang, 45 px par défaut.

Renderer
--------
Le renderer Mermaid est inscrit explicitement dans chaque .mmd :
- dagre-d3      : valeur par défaut ;
- dagre-wrapper : renderer Dagre récent.

Exemples
--------
    python ctm_json_to_mermaid.py fichier.json --show-events false --mode both

    python ctm_json_to_mermaid.py fichier.json \
        --show-events true \
        --mode global \
        --layout vertical \
        --renderer dagre-d3 \
        --rank-spacing 30 \
        --node-spacing 45

Principes de maintenance
------------------------
- Les Events Control-M restent la source de vérité des dépendances.
- L'ordre n'est jamais déduit des noms.
- Un Event conditionnel reste rattaché à son objet producteur réel.
- Les contraintes de layout restent séparées des dépendances métier.
"""

import argparse
import hashlib
import html
import json
import re
import sys
from collections import defaultdict, Counter
from pathlib import Path

SCRIPT_VERSION = "2026.09.09-compact-v3"
DEFAULT_RANK_SPACING = 30
DEFAULT_NODE_SPACING = 45



# ---------------------------------------------------------------------------
# Utilitaires généraux
# ---------------------------------------------------------------------------


def parse_bool(value):
    """
    Convertit une valeur en booléen.

    Valeurs reconnues comme vraies :
    true, yes, 1, y, oui, o.
    Toute autre valeur est considérée comme fausse.
    """
    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in ("true", "yes", "1", "y", "oui", "o")



def clean_id(prefix, text):
    """
    Crée un identifiant technique compatible avec Mermaid.

    Les caractères autres que lettre, chiffre ou "_" sont remplacés par "_".
    Cet identifiant est uniquement interne : le nom Control-M original reste
    utilisé dans le libellé affiché.
    """
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", str(text))
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"{prefix}_{safe}" if safe else prefix



def esc(text):
    """
    Échappe le texte injecté dans un label Mermaid avec htmlLabels=true.

    Important : Mermaid parse d'abord la chaîne du label, puis le HTML.
    Les antislashs du type \" peuvent donc provoquer une erreur de parsing.
    On utilise des entités HTML à la place :
    - " devient &quot;
    - < devient &lt;
    - > devient &gt;
    - & devient &amp;
    Les retours ligne deviennent <br/> pour garder un affichage multi-lignes.
    """
    escaped = html.escape(str(text), quote=True)
    return escaped.replace("\n", "<br/>")



def short(text, max_len=60):
    """
    Raccourcit les textes très longs.

    Utilisé surtout pour les commandes, scripts et planifications.
    Sans ça, certains jobs deviennent énormes dans le diagramme.
    """
    if not text:
        return ""

    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= max_len else text[:max_len] + "..."



def small(text):
    """
    Retourne un morceau de HTML affiché plus petit dans Mermaid.

    Mermaid accepte ce HTML car on active htmlLabels dans write_header().
    On l'utilise pour les détails secondaires : host, user, commande, planif.
    """
    return f"<span style='font-size:10px'>{esc(text)}</span>"



def get_events(block):
    """
    Extrait les noms d'Events d'un bloc Control-M.

    Les blocs concernés ressemblent à ceci :

        "eventsToAdd": {
          "Type": "AddEvents",
          "Events": [
            {"Event": "EVENT_A"},
            {"Event": "EVENT_B"}
          ]
        }

    La même structure est utilisée pour :
    - eventsToAdd      : Events produits ;
    - eventsToWaitFor  : Events attendus ;
    - eventsToDelete   : Events supprimés, non utilisé ici pour les flèches.
    """
    events = []

    if isinstance(block, dict):
        for item in block.get("Events", []):
            if isinstance(item, dict) and item.get("Event"):
                events.append(item["Event"])

    return events



def is_notok_status(status):
    """
    Détermine si un statut Control-M correspond à un chemin d'erreur.

    Deux familles de valeurs sont rencontrées dans les exports réels :

    1. Statuts textuels :
       NOTOK, NOT_OK, NOK, FAILED, FAILURE, ERROR (-> NOTOK)
       OK, SUCCESS (-> OK)

    2. Codes retour numériques :
       Certains blocs If:CompletionStatus utilisent un code retour de step
       plutôt qu'un statut texte, par exemple "CompletionStatus": "0".
       Convention Control-M/Unix habituelle : 0 = succès, tout code non nul
       = échec. Sans cette règle, un If:CompletionStatus "4" ou "8" (échec)
       était silencieusement traité comme OK et la flèche restait verte au
       lieu de rouge/orange.

    Cette convention (0=OK, non-nul=NOTOK) est une hypothèse raisonnable mais
    reste une hypothèse : si un export utilise des codes retour applicatifs
    où un code non nul est normal, il faudra l'ajuster ici.
    """
    if status is None:
        return False

    value = str(status).strip()
    upper = value.upper().replace(" ", "")

    if upper in ("NOTOK", "NOT_OK", "NOK", "FAILED", "FAILURE", "ERROR"):
        return True

    if upper in ("OK", "SUCCESS"):
        return False

    if re.fullmatch(r"-?\d+", value):
        return value != "0"

    return False


# ---------------------------------------------------------------------------
# Modèle mémoire Control-M
# ---------------------------------------------------------------------------


class ControlMModel:
    """
    Modèle mémoire unique de l'export Control-M.

    Le JSON est parcouru une seule fois. La classe conserve la hiérarchie des
    objets ainsi que les producteurs/consommateurs d'Events nécessaires aux
    rendus global et SubFolder.
    """

    def __init__(self):
        """
        Initialise toutes les structures de données.

        objects :
            Dictionnaire path -> objet Control-M.
            Exemple de path :
                FOLDER/SUBFOLDER/JOB

        children :
            Dictionnaire parent_path -> liste des chemins enfants.
            Permet de reconstruire la hiérarchie Mermaid avec subgraph.

        event_producers :
            Dictionnaire event -> chemins des objets qui produisent cet Event.

        event_consumers :
            Dictionnaire event -> chemins des objets qui attendent cet Event.

        producer_event_statuses :
            Dictionnaire event -> producteur -> statuts OK/NOTOK.
            Sert à colorer les flèches : vert, rouge ou orange.
        """
        self.objects = {}
        self.children = defaultdict(list)
        self.event_producers = defaultdict(set)
        self.event_consumers = defaultdict(set)
        self.producer_event_statuses = defaultdict(lambda: defaultdict(set))

        # Index inverse path -> Events touchés. En mode SubFolder, il évite de
        # rescanner les Events sans rapport avec le sous-arbre courant.
        self.events_by_object = defaultdict(set)

    def events_touching(self, paths):
        """
        Retourne l'ensemble des events produits ou consommés par un groupe
        d'objets (typiquement : tous les objets d'un sous-arbre SubFolder).
        """
        result = set()
        for path in paths:
            result.update(self.events_by_object.get(path, ()))
        return result

    def register_object(self, path, name, obj_type, parent, node):
        """
        Enregistre un objet Control-M trouvé dans le JSON.

        Cette fonction est appelée pour chaque Folder, SubFolder ou Job détecté.
        Elle ne décide pas des dépendances : elle ne fait que mémoriser l'objet
        et sa position dans la hiérarchie.

        Paramètres :
            path :
                Chemin technique unique dans le modèle. Exemple :
                FOLDER/SUBFOLDER/JOB.
                Si un job apparaît plusieurs fois avec le même Name, le path peut
                contenir un suffixe __OCC_n pour éviter une collision Mermaid.

            name :
                Nom Control-M affiché dans le diagramme. Contrairement au path,
                il reste inchangé afin que le graphe montre les vrais noms métier.

            obj_type :
                Type Control-M : Folder, SubFolder, Job:Command, Job:Script, etc.

            parent :
                Path technique du parent. Sert à reconstruire les subgraph Mermaid.

            node :
                Bloc JSON complet. On le garde pour construire les labels et pour
                retrouver les propriétés utiles plus tard.
        """
        self.objects[path] = {
            "id": clean_id("N", path),
            "name": name,
            "type": obj_type,
            "parent": parent,
            "node": node,
        }

        # Mémorise la hiérarchie nécessaire aux subgraphs Mermaid.
        if parent:
            self.children[parent].append(path)

    def add_producer(self, event, path, status):
        """
        Enregistre qu'un objet produit un Event Control-M.

        status vaut généralement :
        - OK    : Event produit sur chemin nominal ;
        - NOTOK : Event produit sur chemin d'erreur.

        Pourquoi stocker un set de statuts ?
        ------------------------------------
        Un même event peut être présent dans eventsToAdd et également dans un
        bloc conditionnel If:CompletionStatus. Dans ce cas, le même lien logique
        peut exister en OK et en NOTOK. On doit garder les deux informations pour
        colorer la flèche en orange.

        Exemple abstrait :
            A produit EVENT_X en OK ;
            A produit aussi EVENT_X en NOTOK ;
            B attend EVENT_X.

        Le lien A -> B doit alors être orange, pas seulement vert ou rouge.
        """
        self.event_producers[event].add(path)
        self.producer_event_statuses[event][path].add(status)
        self.events_by_object[path].add(event)

    def collect_event_adds_inside(self, node, inherited_status=None):
        """
        Recherche récursivement les Event:Add internes à un job.

        Control-M peut produire des events de deux manières :

        1. Directement avec eventsToAdd
           Ces events sont considérés comme des productions OK.

        2. Dans une action conditionnelle
           Exemple :

               If:CompletionStatus NOTOK
                   Event:Add EVENT_A

           Ici, EVENT_A ne doit pas être traité comme une dépendance nominale.
           Il représente un chemin d'erreur, donc une flèche rouge.

        Cette fonction descend récursivement dans le bloc JSON du job pour trouver
        ces Event:Add conditionnels. Elle supporte :
        - les sous-blocs dictionnaires nommés : "IfBase...": { ... } ;
        - les sous-blocs dans des listes : "Actions": [ { ... } ].

        inherited_status permet de transporter le statut du bloc If parent vers
        l'action Event:Add située plus bas dans l'arbre JSON.
        """
        found = []

        if isinstance(node, list):
            for item in node:
                found.extend(self.collect_event_adds_inside(item, inherited_status))
            return found

        if not isinstance(node, dict):
            return found

        current_status = inherited_status

        # Si on entre dans un bloc conditionnel, on mémorise le statut.
        if node.get("Type") == "If:CompletionStatus":
            current_status = node.get("CompletionStatus", inherited_status)

        # Si on trouve un Event:Add, on le classe OK ou NOTOK selon le contexte.
        if node.get("Type") == "Event:Add" and node.get("Event"):
            status = "NOTOK" if is_notok_status(current_status) else "OK"
            found.append((node["Event"], status))

        # On continue à descendre dans les sous-blocs techniques.
        for value in node.values():
            if isinstance(value, list):
                found.extend(self.collect_event_adds_inside(value, current_status))
                continue

            if not isinstance(value, dict):
                continue

            child_type = value.get("Type", "")

            # Sécurité importante : on ne traverse pas les vrais objets Control-M
            # enfants depuis ici, sinon un Folder ou un Job pourrait récupérer à
            # tort les Events d'un autre objet.
            if child_type in ("Folder", "SubFolder") or child_type.startswith("Job"):
                continue

            found.extend(self.collect_event_adds_inside(value, current_status))

        return found

    def walk(self, node, name=None, parent=None):
        """
        Parcourt récursivement tout le JSON Control-M.

        Supporte les deux formats rencontrés :
        - format "clé nommée" : "JOB_A": {"Type": "Job:Command", ...}
        - format "liste Jobs" : "Jobs": [{"Type": "Job:Command", "Name": "JOB_A", ...}]

        À chaque objet trouvé, on calcule son path complet et on l'enregistre.
        """
        if isinstance(node, list):
            # Format Jobs:[...]. Les noms viennent du champ Name. Les doublons
            # de (Type, Name) sont numérotés afin de conserver chaque occurrence
            # comme un nœud Mermaid distinct.
            duplicate_keys = []
            for item in node:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("Type", "")
                item_name = item.get("Name") or item.get("FolderName") or item.get("SubFolderName")
                if item_type in ("Folder", "SubFolder") or item_type.startswith("Job"):
                    duplicate_keys.append((item_type, item_name))

            duplicate_counts = Counter(duplicate_keys)
            duplicate_seen = defaultdict(int)

            for index, item in enumerate(node):
                if not isinstance(item, dict):
                    continue

                item_type = item.get("Type", "")
                item_name = item.get("Name") or item.get("FolderName") or item.get("SubFolderName")

                # Dans les exports avec "Jobs": [...], le nom du job est dans "Name".
                # S'il manque, on garde un nom technique stable pour éviter un path None.
                if item_type in ("Folder", "SubFolder") or item_type.startswith("Job"):
                    item_name = item_name or f"{name or 'ITEM'}_{index + 1}"
                    key = (item_type, item_name)
                    if duplicate_counts.get(key, 0) > 1:
                        duplicate_seen[key] += 1
                        item["_ctm_duplicate_total"] = duplicate_counts[key]
                        item["_ctm_duplicate_index"] = duplicate_seen[key]

                self.walk(item, item_name, parent)
            return

        if not isinstance(node, dict):
            return

        node_type = node.get("Type", "")
        is_folder = node_type in ("Folder", "SubFolder")
        is_job = node_type.startswith("Job")
        current_path = parent

        if is_folder or is_job:
            # Format dictionnaire : le nom arrive par la clé JSON.
            # Format liste Jobs : le nom arrive par node["Name"].
            object_name = name or node.get("Name") or node.get("FolderName") or node.get("SubFolderName")
            if not object_name:
                # Nom de secours déterministe : un hash du contenu JSON évite
                # qu'un objet anonyme change d'ID Mermaid entre deux exécutions.
                fallback_digest = hashlib.sha1(
                    json.dumps(node, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
                ).hexdigest()[:8]
                object_name = node.get("FileName") or clean_id("OBJECT", fallback_digest)
                print(
                    f"⚠️  Objet {node_type or 'inconnu'} sans Name/FolderName/SubFolderName "
                    f"sous '{parent or 'racine'}' -> nom de secours utilisé : {object_name}"
                )

            base_path = f"{parent}/{object_name}" if parent else object_name

            duplicate_total = int(node.get("_ctm_duplicate_total") or 0)
            duplicate_index = int(node.get("_ctm_duplicate_index") or 0)

            # Si deux jobs du même parent portent le même Name, Mermaid ne doit pas
            # recevoir deux fois le même ID technique. On suffixe uniquement l'ID/path
            # interne ; le label affiché reste le Name Control-M original.
            if is_job and duplicate_total > 1:
                current_path = f"{base_path}__OCC_{duplicate_index}"
            else:
                current_path = base_path

            self.register_object(current_path, object_name, node_type, parent, node)

            direct_adds = get_events(node.get("eventsToAdd"))
            waits = get_events(node.get("eventsToWaitFor"))
            conditional_adds = self.collect_event_adds_inside(node) if is_job else []

            if is_job and duplicate_total > 1:
                # Occurrences répétées du même Name : elles restent distinctes.
                # Pour éviter les liaisons many-to-many entre occurrences identiques,
                # on retient l'attente principale et les productions pertinentes
                # fournies par le JSON, sans règle basée sur le nom du job.
                main_wait = waits[-1] if waits else None
                main_cond_event = conditional_adds[-1][0] if conditional_adds else (direct_adds[-1] if direct_adds else None)

                if main_wait:
                    self.event_consumers[main_wait].add(current_path)
                    self.events_by_object[current_path].add(main_wait)

                for event in direct_adds:
                    if event == main_cond_event:
                        self.add_producer(event, current_path, "OK")

                for event, status in conditional_adds:
                    self.add_producer(event, current_path, status)

            else:
                # Events produits directement par l'objet.
                # Par convention, on considère eventsToAdd comme chemin OK.
                for event in direct_adds:
                    self.add_producer(event, current_path, "OK")

                # Events produits à l'intérieur d'un job dans des blocs conditionnels.
                # Cela permet de détecter notamment les Events produits en NOTOK.
                if is_job:
                    for event, status in conditional_adds:
                        self.add_producer(event, current_path, status)

                # Events attendus par l'objet.
                # Une dépendance sera créée si un autre objet produit le même Event.
                for event in waits:
                    self.event_consumers[event].add(current_path)
                    self.events_by_object[current_path].add(event)

        # Parcours des conteneurs enfants ; current_path devient leur parent.
        for key, value in node.items():
            # Déjà traités comme métadonnées de dépendance de l'objet courant.
            if key in ("eventsToAdd", "eventsToWaitFor", "eventsToDelete"):
                continue

            if isinstance(value, dict):
                self.walk(value, key, current_path)
            elif isinstance(value, list):
                self.walk(value, key, current_path)

    def roots(self):
        """
        Retourne les objets sans parent.

        Dans un export classique, c'est le Folder racine.
        """
        return [path for path, obj in self.objects.items() if not obj["parent"]]

    def subfolders(self):
        """
        Retourne tous les SubFolders du modèle.

        Chaque SubFolder pourra donner un fichier Mermaid séparé.
        """
        return [path for path, obj in self.objects.items() if obj["type"] == "SubFolder"]

    def root_folder_jobs(self):
        """
        Retourne les Jobs directement sous un Folder racine.

        Méthode conservée comme utilitaire de diagnostic pour les exports qui
        mélangent Jobs à plat et SubFolders. Elle n'est pas utilisée par le
        chemin de génération courant.
        """
        result = set()

        for path, obj in self.objects.items():
            if not obj["type"].startswith("Job"):
                continue

            parent = obj.get("parent")
            parent_obj = self.objects.get(parent)

            if parent_obj and parent_obj["type"] == "Folder" and not parent_obj.get("parent"):
                result.add(path)

        return result

    def collect_subtree_paths(self, root):
        """
        Retourne tous les objets contenus dans un sous-arbre.

        Utilisé pour générer un .mmd local à un SubFolder :
        - le SubFolder lui-même ;
        - tous ses Jobs ;
        - éventuellement ses sous-SubFolders si le JSON en contient.
        """
        result = set()

        def dfs(path):
            result.add(path)
            for child in self.children.get(path, []):
                dfs(child)

        dfs(root)
        return result

    def object_anchor(self, path):
        """
        Retourne l'ID Mermaid de l'objet utilisé comme extrémité d'une flèche.

        Pour un Job, il s'agit de l'ID du nœud.
        Pour un Folder/SubFolder, il s'agit directement de l'ID du subgraph.

        Aucun nœud d'ancrage intermédiaire n'est créé : les liens vers un
        Folder/SubFolder ciblent donc directement son cadre Mermaid.
        """
        return self.objects[path]["id"]


# ---------------------------------------------------------------------------
# Construction des labels affichés dans Mermaid
# ---------------------------------------------------------------------------


def when_label(node):
    """
    Extrait une planification courte depuis un objet Control-M.

    On tente plusieurs noms de blocs possibles :
    - When ;
    - Scheduling ;
    - Schedule.

    Point important sur USE PARENT
    ------------------------------
    Dans Control-M, beaucoup de jobs et de SubFolders n'ont pas leur propre
    calendrier. Ils héritent de la planification du Folder parent. Dans le JSON,
    cela apparaît souvent ainsi :

        "RuleBasedCalendars": {
            "Included": ["USE PARENT"]
        }

    ou parfois directement dans certains champs de planning.

    Ce cas ne doit pas être affiché comme NONE :
    - NONE veut dire "aucune valeur utile trouvée" ;
    - USE PARENT veut dire "la planification existe, mais elle est héritée".

    Règle appliquée ici :
    - on masque toujours les valeurs purement techniques NONE ;
    - on détecte USE PARENT et on l'affiche si aucune autre information plus
      précise n'est disponible ;
    - si un vrai planning est présent, par exemple FromTime ou un calendrier
      explicite, on affiche ce planning plutôt que de le remplacer par USE PARENT.
    """
    when = node.get("When") or node.get("Scheduling") or node.get("Schedule")

    if not isinstance(when, dict):
        return ""

    # USE PARENT n'est affiché que si aucune information plus précise n'existe.
    use_parent_found = False

    def clean(value):
        """
        Nettoie une valeur de planning.

        - NONE est ignoré, car il n'apporte aucune information.
        - USE PARENT est détecté, mais pas renvoyé directement ici : il est géré
          à la fin pour éviter d'afficher des choses comme "CAL:USE PARENT".
        - les vraies valeurs sont conservées.
        """
        nonlocal use_parent_found

        if not value:
            return None

        if isinstance(value, list):
            cleaned = []
            for item in value:
                if not item or item == "NONE":
                    continue
                if item == "USE PARENT":
                    use_parent_found = True
                    continue
                cleaned.append(item)
            return cleaned if cleaned else None

        if value == "USE PARENT":
            use_parent_found = True
            return None

        if value in ("NONE", "", None):
            return None

        return value

    parts = []

    # Plage horaire ou heure de début.
    from_time = when.get("FromTime") or when.get("StartTime")
    to_time = when.get("ToTime") or when.get("EndTime")

    if from_time and to_time:
        parts.append(f"{from_time}-{to_time}")
    elif from_time:
        parts.append(str(from_time))

    # Jours de semaine ou jours génériques.
    days = clean(when.get("WeekDays") or when.get("Days"))
    if isinstance(days, list):
        parts.append(",".join(days))
    elif days:
        parts.append(str(days))

    # Mois ou jours du mois selon la structure d'export.
    months = clean(when.get("Months") or when.get("MonthDays"))
    if isinstance(months, list):
        parts.append("M:" + ",".join(months))
    elif months:
        parts.append("M:" + str(months))

    # Calendriers Control-M.
    cal = when.get("RuleBasedCalendars")
    if isinstance(cal, dict):
        inc = clean(cal.get("Included"))
        if inc:
            parts.append("CAL:" + (",".join(inc) if isinstance(inc, list) else str(inc)))

    # Relation entre règles de jours, seulement si elle apporte une information.
    rel = when.get("DaysRelation")
    if rel and rel != "OR":
        parts.append(f"REL:{rel}")

    if parts:
        return " ".join(parts)

    if use_parent_found:
        return "USE PARENT"

    return "NONE"



def _first_present(mapping, *keys):
    """Retourne la première valeur présente parmi plusieurs noms de propriétés."""
    if not isinstance(mapping, dict):
        return None

    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]

    # Fallback insensible à la casse pour des variantes d'export.
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        value = lowered.get(str(key).lower())
        if value not in (None, ""):
            return value

    return None


def _truthy(value):
    """Interprète les variantes usuelles de booléens rencontrées dans les exports."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y", "oui", "o", "on")


def _format_hhmm(value):
    """Transforme 0900 en 09:00 lorsque la valeur ressemble à une heure HHMM."""
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        return f"{text[:2]}:{text[2:]}"
    return text


def _compact_unit(unit):
    """Raccourcit les unités Control-M pour garder les labels lisibles."""
    value = str(unit or "Minutes").strip().lower()
    return {
        "minute": "min",
        "minutes": "min",
        "min": "min",
        "hour": "h",
        "hours": "h",
        "h": "h",
        "day": "j",
        "days": "j",
        "d": "j",
        "month": "mois",
        "months": "mois",
    }.get(value, str(unit or "Minutes"))


def _from_label(value):
    """Libellé court du point de référence des cycles Control-M."""
    normalized = str(value or "Start").strip().lower()
    return {
        "start": "depuis début",
        "end": "depuis fin",
        "target": "sur horaire cible",
        "strt": "depuis début",
    }.get(normalized, f"depuis {value}" if value else "depuis début")


def cyclic_label(node):
    """
    Retourne un libellé court lorsqu'un Job/Folder est cyclique.

    Structures reconnues par le script :
      - Rerun              : intervalle fixe ;
      - RerunIntervals     : séquence d'intervalles ;
      - RerunSpecificTimes : heures précises ;
      - Cyclic et quelques variantes d'export : fallback.

    RerunLimit seul n'est volontairement pas utilisé comme indicateur de
    cyclicité.
    """
    if not isinstance(node, dict):
        return ""

    # 1) Intervalle fixe : {"Rerun": {"Every":"2", "Units":"Minutes", ...}}
    rerun = _first_present(node, "Rerun")
    if isinstance(rerun, dict):
        every = _first_present(rerun, "Every")
        if every is not None:
            every_text = str(every).strip()
            units = _compact_unit(_first_present(rerun, "Units") or "Minutes")
            origin = _from_label(_first_present(rerun, "From") or "Start")
            times = _first_present(rerun, "Times")

            if every_text in ("0", "0.0"):
                cadence = "continu"
            else:
                cadence = f"toutes les {every_text} {units}"

            if times in (None, "", 0, "0"):
                repeat = "∞"
            else:
                repeat = f"{times} cycles"

            return f"🔁 Cyclique: {cadence} · {origin} · {repeat}"

    # 2) Séquence d'intervalles.
    intervals = _first_present(node, "RerunIntervals")
    if isinstance(intervals, dict):
        values = _first_present(intervals, "Intervals")
        if values:
            if not isinstance(values, list):
                values = [values]
            seq = " → ".join(str(v) for v in values)
            origin = _from_label(_first_present(intervals, "From") or "Start")
            return f"🔁 Cyclique: {seq} · {origin}"

    # 3) Heures spécifiques.
    specific = _first_present(node, "RerunSpecificTimes")
    if isinstance(specific, dict):
        values = _first_present(specific, "At")
        if values:
            if not isinstance(values, list):
                values = [values]
            at = ", ".join(_format_hhmm(v) for v in values)
            tolerance = _first_present(specific, "Tolerance")
            suffix = f" · tol. {tolerance} min" if tolerance not in (None, "") else ""
            return f"🔁 Cyclique: {at}{suffix}"

    # 4) Fallback pour des structures d'export alternatives.
    cyclic_flag = _first_present(node, "Cyclic", "CYCLIC", "cyclic")
    if _truthy(cyclic_flag):
        every = _first_present(node, "RerunEvery", "rerun_interval", "INTERVAL")
        units = _first_present(node, "RerunUnits", "IntervalUnits", "Units")
        max_runs = _first_present(node, "MaxReruns", "rerun_max", "MAXRERUN")

        details = []
        if every not in (None, ""):
            details.append(f"toutes les {every} {_compact_unit(units or 'Minutes')}")
        if max_runs not in (None, ""):
            details.append(f"max {max_runs}")

        return "🔁 Cyclique" + (": " + " · ".join(details) if details else "")

    return ""

def job_label(name, node):
    """
    Construit le label affiché pour un Job.

    Le label peut contenir : nom, Host/HostGroup, RunAs/User, planification,
    indication cyclique et Command/Script/FileName.
    """
    lines = [f"⚙️ {esc(name)}"]

    host = node.get("Host") or node.get("HostGroup")
    run_as = node.get("RunAs") or node.get("User")
    command = node.get("Command")
    script = node.get("Script")
    file_name = node.get("FileName")
    schedule = when_label(node)
    cycle = cyclic_label(node)

    if host:
        lines.append(small(f"Host: {host}"))

    if run_as:
        lines.append(small(f"User: {run_as}"))

    if schedule:
        lines.append(small(f"Planif: {short(schedule, 90)}"))

    if cycle:
        lines.append(small(short(cycle, 120)))

    if command:
        lines.append(small(f"Cmd: {short(command)}"))
    elif script:
        lines.append(small(f"Script: {short(script)}"))
    elif file_name:
        lines.append(small(f"File: {file_name}"))

    return "<br/>".join(lines)



def folder_label(name, node, obj_type, force_schedule=False):
    """
    Construit le titre natif d'un Folder/SubFolder sur une seule ligne.

    La planification est affichée :
    - sur le Folder ;
    - sur le SubFolder lorsqu'il est la racine d'un .mmd dédié.

    L'information cyclique est ajoutée lorsqu'elle est disponible.
    Le titre reste mono-ligne afin de conserver le centrage natif Mermaid et
    d'éviter de créer un nœud de mise en page supplémentaire.
    """
    parts = [f"📁 {esc(name)}"]

    if obj_type == "Folder" or force_schedule:
        schedule = when_label(node)
        if schedule:
            parts.append(small("Planif: " + short(schedule, 90)))

    cycle = cyclic_label(node)
    if cycle:
        parts.append(small(short(cycle, 100)))

    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Construction des dépendances / flèches Mermaid
# ---------------------------------------------------------------------------


def edge_color(statuses):
    """
    Détermine la couleur logique d'une flèche.

    Règles :
    - OK uniquement       -> vert ;
    - NOTOK uniquement    -> rouge ;
    - OK + NOTOK ensemble -> orange.
    """
    if "OK" in statuses and "NOTOK" in statuses:
        return "orange"

    if "NOTOK" in statuses:
        return "red"

    return "green"



def build_edges(model, scope_paths=None, include_orphans=True, excluded_paths=None):
    """
    Construit les dépendances Mermaid à partir des Events du modèle Control-M.

    Règle principale
    ----------------
    Une flèche n'existe que lorsqu'un même Event possède :
    - au moins un producteur ;
    - au moins un consommateur.

    Le producteur reste l'objet qui porte réellement l'Event. Ainsi, un
    Event:Add NOTOK défini dans un Job produit une flèche depuis ce Job, même si
    son SubFolder produit par ailleurs le même Event via eventsToAdd.

    Fusion
    ------
    Plusieurs Events entre le même couple source -> cible sont regroupés dans
    une seule flèche. Les statuts sont fusionnés :
    {OK} -> vert, {NOTOK} -> rouge, {OK, NOTOK} -> orange.

    Scope
    -----
    scope_paths=None :
        diagramme global.

    scope_paths=set(...) :
        diagramme local d'un SubFolder ; producteur et consommateur doivent
        appartenir au sous-arbre.

    Orphelins
    ---------
    include_orphans=True n'a d'effet que dans la vue globale :
    les Events sans producteur ou sans consommateur sont matérialisés par un
    nœud dédié afin de rendre les dépendances incomplètes visibles.
    """
    edge_map = {}
    scope = set(scope_paths) if scope_paths is not None else None
    excluded = set(excluded_paths or [])

    # Global : tous les Events sont examinés, notamment pour les orphelins.
    # SubFolder : seuls les Events touchant le sous-arbre sont parcourus.
    if scope is None:
        all_events = sorted(set(model.event_producers) | set(model.event_consumers))
    else:
        all_events = sorted(model.events_touching(scope))

    def in_scope(path):
        """
        Indique si un objet est autorisé dans le diagramme courant.
        """
        return path not in excluded and (scope is None or path in scope)

    for event in all_events:
        producers = sorted(p for p in model.event_producers[event] if in_scope(p))
        consumers = sorted(c for c in model.event_consumers[event] if in_scope(c))

        if producers and consumers:
            # Cas normal : au moins un producteur et au moins un consommateur.
            for producer in producers:
                statuses = model.producer_event_statuses[event].get(producer, {"OK"})

                for consumer in consumers:
                    source = model.object_anchor(producer)
                    target = model.object_anchor(consumer)
                    key = (source, target)

                    # edge_map évite de créer plusieurs flèches identiques entre
                    # les mêmes nœuds. Si plusieurs Events existent entre les deux,
                    # ils sont regroupés sur la même flèche.
                    edge_map.setdefault(key, {
                        "source": source,
                        "target": target,
                        "events": set(),
                        "statuses": set(),
                    })

                    edge_map[key]["events"].add(event)
                    edge_map[key]["statuses"].update(statuses)

        elif include_orphans and scope is None:
            # Les orphelins ne sont affichés que dans le diagramme global.
            # Cela permet d'identifier les Events incomplets sans polluer les
            # diagrammes détaillés par SubFolder.

            if producers and not model.event_consumers[event]:
                # Event produit mais jamais attendu.
                for producer in producers:
                    statuses = model.producer_event_statuses[event].get(producer, {"OK"})
                    source = model.object_anchor(producer)
                    orphan_id = clean_id("ORPHAN_OUT", f"{producer}/{event}")

                    edge_map[(source, orphan_id)] = {
                        "source": source,
                        "target": orphan_id,
                        "events": {event},
                        "statuses": set(statuses),
                        "orphan": {
                            "id": orphan_id,
                            "label": f"Event non consommé<br/>{esc(event)}",
                        },
                    }

            elif consumers and not model.event_producers[event]:
                # Event attendu mais jamais produit.
                for consumer in consumers:
                    target = model.object_anchor(consumer)
                    orphan_id = clean_id("ORPHAN_IN", f"{consumer}/{event}")

                    edge_map[(orphan_id, target)] = {
                        "source": orphan_id,
                        "target": target,
                        "events": {event},
                        "statuses": {"OK"},
                        "orphan": {
                            "id": orphan_id,
                            "label": f"Event sans producteur<br/>{esc(event)}",
                        },
                    }

    return list(edge_map.values())


# ---------------------------------------------------------------------------
# Rendu Mermaid
# ---------------------------------------------------------------------------


def write_header(
    out,
    renderer="dagre-d3",
    rank_spacing=DEFAULT_RANK_SPACING,
    node_spacing=DEFAULT_NODE_SPACING,
):
    """
    Écrit la configuration Mermaid commune à tous les fichiers .mmd.

    - renderer : moteur de layout demandé ;
    - rank_spacing : espacement vertical entre rangs ;
    - node_spacing : espacement entre nœuds d'un même rang ;
    - curve="linear" : trajectoire plus directe, utile pour distinguer les liens
      Job -> SubFolder, notamment les chemins NOTOK.

    La version du script est écrite en commentaire dans le .mmd pour faciliter
    le diagnostic d'un artifact Azure DevOps.
    """
    out.write(f"%% Generated by ctm_json_to_mermaid.py {SCRIPT_VERSION}\n")
    out.write(
        f'%%{{init: {{"flowchart": {{"defaultRenderer": "{renderer}", '
        f'"rankSpacing": {int(rank_spacing)}, "nodeSpacing": {int(node_spacing)}, '
        '"curve": "linear", "htmlLabels": true}, '
        '"themeVariables": {"fontSize": "12px"}} }}%%\n'
    )

    out.write("flowchart TD\n\n")
    out.write("classDef job fill:#eef6ff,stroke:#7aa7d9,color:#1f2937\n")
    out.write("classDef orphan fill:#fff7ed,stroke:#f0b37e,color:#1f2937\n\n")


def topo_order_children(model, parent_path, allowed_paths=None):
    """
    Ordonne les enfants directs d'un Folder/SubFolder à partir des vraies
    conditions Control-M Events.

    Principe :
    - aucun ordre n'est déduit du nom des objets ;
    - chaque enfant direct représente un sous-arbre ;
    - si A produit un Event attendu dans B, A précède B ;
    - à niveau égal, le tri reste stable et conserve l'ordre JSON.
    """
    allowed = set(allowed_paths) if allowed_paths is not None else None
    excluded = getattr(model, "_excluded_parent_jobs", set())

    children = [
        child for child in model.children.get(parent_path, [])
        if child not in excluded and (allowed is None or child in allowed)
    ]

    if len(children) <= 1:
        return children

    original_index = {child: i for i, child in enumerate(children)}

    subtree_cache = {}

    def subtree(path):
        if path in subtree_cache:
            return subtree_cache[path]
        result = set()

        def dfs(p):
            if p in excluded:
                return
            if allowed is not None and p not in allowed:
                return
            result.add(p)
            for c in model.children.get(p, []):
                dfs(c)

        dfs(path)
        subtree_cache[path] = result
        return result

    owner_by_object = {}
    for child in children:
        for p in subtree(child):
            owner_by_object[p] = child

    graph = {child: set() for child in children}
    indegree = {child: 0 for child in children}

    # Pour chaque event, on regarde dans quel enfant direct se trouve le producteur
    # et dans quel enfant direct se trouve le consommateur.
    all_events = set(model.event_producers) | set(model.event_consumers)
    for event in all_events:
        producer_owners = {
            owner_by_object[p]
            for p in model.event_producers.get(event, set())
            if p in owner_by_object
        }
        consumer_owners = {
            owner_by_object[c]
            for c in model.event_consumers.get(event, set())
            if c in owner_by_object
        }

        for src in producer_owners:
            for dst in consumer_owners:
                if src == dst:
                    continue
                if dst not in graph[src]:
                    graph[src].add(dst)
                    indegree[dst] += 1

    # Tri topologique stable : à niveau égal, on garde l'ordre JSON d'origine.
    ready = sorted([c for c in children if indegree[c] == 0], key=original_index.get)
    ordered = []

    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for nxt in sorted(graph[current], key=original_index.get):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=original_index.get)

    # Si cycle ou cas ambigu, on garde les nœuds restants en ordre JSON pour ne
    # jamais perdre de jobs dans le rendu.
    if len(ordered) < len(children):
        remaining = [c for c in children if c not in ordered]
        ordered.extend(sorted(remaining, key=original_index.get))

    return ordered

def write_object(
    out,
    model,
    path,
    allowed_paths=None,
    indent=1,
    subfolder_root=None,
    layout_mode="vertical",
):
    """
    Écrit récursivement la hiérarchie Control-M en syntaxe Mermaid.

    - Folder/SubFolder -> subgraph ;
    - Job              -> nœud classique.

    Aucun nœud technique de mise en page n'est ajouté à la hiérarchie.
    """
    allowed = set(allowed_paths) if allowed_paths is not None else None

    if allowed is not None and path not in allowed:
        return

    obj = model.objects[path]
    spaces = "    " * indent

    if obj["type"] in ("Folder", "SubFolder"):
        force_schedule = path == subfolder_root
        label = folder_label(
            obj["name"],
            obj["node"],
            obj["type"],
            force_schedule=force_schedule,
        )

        out.write(f'{spaces}subgraph {obj["id"]}["{label}"]\n')

        if layout_mode == "vertical":
            out.write(f"{spaces}    direction TB\n")

        for child in topo_order_children(model, path, allowed):
            write_object(
                out,
                model,
                child,
                allowed,
                indent + 1,
                subfolder_root,
                layout_mode,
            )

        out.write(f"{spaces}end\n\n")

    elif obj["type"].startswith("Job"):
        label = job_label(obj["name"], obj["node"])
        out.write(f'{spaces}{obj["id"]}["{label}"]:::job\n')


def build_vertical_layout_constraints(model, roots, allowed_paths=None):
    """
    Construit les contraintes graphiques du mode "vertical".

    Pour des SubFolders frères :
    1. les Events Control-M servent uniquement à déterminer quelles chaînes sont
       déjà réellement reliées ;
    2. ces chaînes sont regroupées en composantes connexes ;
    3. une seule contrainte transparente est ajoutée entre deux composantes
       indépendantes successives.

    Ces liens n'ont aucune signification Control-M. Ils servent uniquement à
    éviter que Mermaid place plusieurs chaînes indépendantes côte à côte.
    """
    allowed = set(allowed_paths) if allowed_paths is not None else None
    excluded = getattr(model, "_excluded_parent_jobs", set())
    constraints = []
    seen = set()

    def direct_children(parent_path):
        return [
            child for child in model.children.get(parent_path, [])
            if child not in excluded and (allowed is None or child in allowed)
        ]

    def subtree(path):
        result = set()

        def dfs(p):
            if p in excluded:
                return
            if allowed is not None and p not in allowed:
                return
            result.add(p)
            for c in model.children.get(p, []):
                dfs(c)

        dfs(path)
        return result

    def visit(parent_path):
        if parent_path in seen:
            return
        seen.add(parent_path)

        ordered_all = topo_order_children(model, parent_path, allowed)
        groups = [
            child for child in ordered_all
            if child in model.objects
            and model.objects[child]["type"] in ("Folder", "SubFolder")
        ]

        if len(groups) > 1:
            order_index = {child: i for i, child in enumerate(groups)}

            # Chaque objet du sous-arbre est rattaché à son SubFolder frère.
            owner_by_object = {}
            for group in groups:
                for obj_path in subtree(group):
                    owner_by_object[obj_path] = group

            # Graphe NON ORIENTÉ des dépendances réelles entre SubFolders frères.
            # Deux groupes reliés par au moins un Event appartiennent à la même
            # composante et n'ont donc pas besoin d'un lien de layout artificiel
            # entre chacun de leurs éléments.
            neighbors = {group: set() for group in groups}
            all_events = set(model.event_producers) | set(model.event_consumers)

            for event in all_events:
                producer_owners = {
                    owner_by_object[p]
                    for p in model.event_producers.get(event, set())
                    if p in owner_by_object
                }
                consumer_owners = {
                    owner_by_object[c]
                    for c in model.event_consumers.get(event, set())
                    if c in owner_by_object
                }

                for src in producer_owners:
                    for dst in consumer_owners:
                        if src == dst:
                            continue
                        neighbors[src].add(dst)
                        neighbors[dst].add(src)

            # Composantes connexes, ordonnées selon le même ordre topologique que
            # celui utilisé pour écrire les SubFolders dans le fichier Mermaid.
            components = []
            visited = set()

            for group in groups:
                if group in visited:
                    continue

                stack = [group]
                component = []
                visited.add(group)

                while stack:
                    current = stack.pop()
                    component.append(current)
                    for nxt in sorted(neighbors[current], key=order_index.get, reverse=True):
                        if nxt not in visited:
                            visited.add(nxt)
                            stack.append(nxt)

                component.sort(key=order_index.get)
                components.append(component)

            components.sort(key=lambda comp: min(order_index[g] for g in comp))

            # Une seule contrainte entre deux chaînes indépendantes : la fin de la
            # composante précédente vers le début de la suivante.
            for previous, following in zip(components, components[1:]):
                left = previous[-1]
                right = following[0]
                constraints.append((model.object_anchor(left), model.object_anchor(right)))

        for child in groups:
            visit(child)

    for root in roots:
        if root in model.objects and model.objects[root]["type"] in ("Folder", "SubFolder"):
            visit(root)

    return constraints


def write_layout_constraints(out, constraints, start_index):
    """
    Écrit les contraintes de placement sous forme de liens Mermaid `---`.

    Elles sont ensuite masquées avec linkStyle. L'index de départ est fourni
    explicitement pour ne pas décaler les styles des dépendances métier.

    Retour : [(index_du_lien, stroke, width, fill), ...].
    """
    if not constraints:
        return []

    out.write("\n    %% Liens transparents de placement : aucune dépendance Control-M\n")
    styles = []

    for offset, (source, target) in enumerate(constraints):
        out.write(f"    {source} --- {target}\n")
        styles.append((start_index + offset, "transparent", "0px", "transparent"))

    return styles


def build_render_order_index(model, roots, allowed_paths=None):
    """
    Construit l'ordre exact de déclaration des nœuds Mermaid, en utilisant le
    même tri topologique que write_object(). Cet ordre sert ensuite à écrire les
    flèches dans le même sens visuel que les nœuds.
    """
    allowed = set(allowed_paths) if allowed_paths is not None else None
    order = []

    def visit(path):
        if allowed is not None and path not in allowed:
            return
        if path in getattr(model, "_excluded_parent_jobs", set()):
            return
        obj = model.objects[path]
        if obj["type"] in ("Folder", "SubFolder"):
            order.append(obj["id"])
            for child in topo_order_children(model, path, allowed):
                visit(child)
        else:
            order.append(obj["id"])

    for root in sorted(roots):
        visit(root)

    return {node_id: index for index, node_id in enumerate(order)}


def sort_edges_for_mermaid_layout(edges, order_index):
    """
    Trie les liens selon l'ordre de déclaration des nœuds. Cela stabilise le
    placement Mermaid sans modifier le sens ni la nature des dépendances.
    """
    missing = len(order_index) + 100000

    def key(edge):
        s = order_index.get(edge["source"], missing)
        t = order_index.get(edge["target"], missing)
        return (min(s, t), s, t, edge["source"], edge["target"])

    return sorted(edges, key=key)

def write_edges(out, edges, show_events):
    """
    Écrit uniquement les vraies flèches Control-M et retourne leurs styles.

    Les `linkStyle` sont volontairement écrits plus tard, après les contraintes
    de layout, afin que le fichier Mermaid contienne d'abord TOUS les liens puis
    seulement leurs styles. C'est plus robuste selon les versions de Mermaid.
    """
    orphan_nodes = {}

    for edge in edges:
        orphan = edge.get("orphan")
        if orphan:
            orphan_nodes[orphan["id"]] = orphan["label"]

    if orphan_nodes:
        out.write("\n")
        for orphan_id, label in sorted(orphan_nodes.items()):
            out.write(f'    {orphan_id}["{label}"]:::orphan\n')

    out.write("\n")
    link_styles = []

    for idx, edge in enumerate(edges):
        source = edge["source"]
        target = edge["target"]
        events = sorted(edge["events"])
        color = edge_color(edge["statuses"])

        if color == "red":
            link_op, stroke, width = "-.->", "#dc2626", "2.5px"
        elif color == "orange":
            link_op, stroke, width = "==>", "#f59e0b", "3px"
        else:
            link_op, stroke, width = "-->", "#16a34a", "2px"

        if show_events:
            label = "<br/>".join(esc(event) for event in events)
            out.write(f'    {source} {link_op}|"{label}"| {target}\n')
        else:
            out.write(f"    {source} {link_op} {target}\n")

        link_styles.append((idx, stroke, width, None))

    return link_styles


def write_link_styles(out, styles):
    """Écrit les linkStyle après que tous les liens Mermaid ont été déclarés."""
    if not styles:
        return

    out.write("\n")
    for idx, stroke, width, fill in styles:
        parts = [f"stroke:{stroke}", f"stroke-width:{width}"]
        if fill is not None:
            parts.append(f"fill:{fill}")
        out.write(f"linkStyle {idx} {','.join(parts)}\n")


def render_mmd(
    model,
    roots,
    output_file,
    show_events,
    allowed_paths=None,
    include_orphans=True,
    subfolder_root=None,
    layout_mode="vertical",
    renderer="dagre-d3",
    rank_spacing=DEFAULT_RANK_SPACING,
    node_spacing=DEFAULT_NODE_SPACING,
):
    """
    Génère un fichier .mmd complet.

    Ordre d'écriture :
    1. configuration Mermaid ;
    2. Folder/SubFolder/Jobs ;
    3. vraies dépendances Control-M ;
    4. contraintes transparentes de layout ;
    5. linkStyle de tous les liens.

    Cet ordre évite que les liens techniques de placement décalent les index de
    style des dépendances métier.
    """
    excluded_parent_jobs = set()

    edges = build_edges(
        model,
        scope_paths=allowed_paths,
        include_orphans=include_orphans,
        excluded_paths=excluded_parent_jobs,
    )
    order_index = build_render_order_index(model, roots, allowed_paths)
    edges = sort_edges_for_mermaid_layout(edges, order_index)

    layout_constraints = []
    if layout_mode == "vertical":
        layout_constraints = build_vertical_layout_constraints(
            model, roots, allowed_paths
        )

    with open(output_file, "w", encoding="utf-8") as out:
        write_header(
            out,
            renderer=renderer,
            rank_spacing=rank_spacing,
            node_spacing=node_spacing,
        )

        for root in sorted(roots):
            write_object(
                out,
                model,
                root,
                allowed_paths,
                1,
                subfolder_root,
                layout_mode,
            )

        business_styles = write_edges(out, edges, show_events)

        layout_styles = write_layout_constraints(
            out,
            layout_constraints,
            start_index=len(edges),
        )

        write_link_styles(out, business_styles + layout_styles)

    print(f"OK -> {output_file}")


def generate_global(
    model,
    input_path,
    output_dir,
    show_events,
    layout_mode="vertical",
    renderer="dagre-d3",
    rank_spacing=DEFAULT_RANK_SPACING,
    node_spacing=DEFAULT_NODE_SPACING,
):
    """Génère le diagramme global du fichier JSON."""
    output_file = output_dir / f"{input_path.stem}.mmd"

    render_mmd(
        model=model,
        roots=model.roots(),
        output_file=output_file,
        show_events=show_events,
        allowed_paths=None,
        include_orphans=True,
        layout_mode=layout_mode,
        renderer=renderer,
        rank_spacing=rank_spacing,
        node_spacing=node_spacing,
    )


def generate_subfolders(
    model,
    input_path,
    output_dir,
    show_events,
    layout_mode="vertical",
    renderer="dagre-d3",
    rank_spacing=DEFAULT_RANK_SPACING,
    node_spacing=DEFAULT_NODE_SPACING,
):
    """Génère un fichier .mmd par SubFolder."""
    for subfolder in sorted(model.subfolders()):
        subtree = model.collect_subtree_paths(subfolder)
        sub_name = clean_id("SUBFOLDER", subfolder)
        output_file = output_dir / f"{input_path.stem}_{sub_name}.mmd"

        render_mmd(
            model=model,
            roots=[subfolder],
            output_file=output_file,
            show_events=show_events,
            allowed_paths=subtree,
            include_orphans=False,
            subfolder_root=subfolder,
            layout_mode=layout_mode,
            renderer=renderer,
            rank_spacing=rank_spacing,
            node_spacing=node_spacing,
        )


# ---------------------------------------------------------------------------
# Interface ligne de commande
# ---------------------------------------------------------------------------


def ask_inputs():
    """
    Demande les paramètres essentiels lorsqu'aucun JSON n'est fourni en CLI.

    Les options avancées gardent leurs valeurs par défaut en mode interactif.
    """
    json_path = input("Nom/chemin du fichier JSON Control-M : ").strip().strip('"')
    show_events_input = input(
        "Afficher les noms des events sur les flèches ? [false/true] : "
    ).strip()
    mode = input("Génération [global/subfolders/both] ? [both] : ").strip().lower() or "both"

    return (
        json_path,
        parse_bool(show_events_input),
        mode,
        "vertical",
        "dagre-d3",
        DEFAULT_RANK_SPACING,
        DEFAULT_NODE_SPACING,
    )


def parse_args():
    """
    Déclare l'interface en ligne de commande.

    Les mêmes options sont utilisées par la pipeline Azure DevOps.
    """
    parser = argparse.ArgumentParser(
        description="Convertit un export JSON Control-M en Mermaid global et/ou SubFolders."
    )

    parser.add_argument("json_file", nargs="?", help="Chemin du fichier JSON Control-M")
    parser.add_argument(
        "legacy_show_events",
        nargs="?",
        help="Compatibilité ancien format: true/false",
    )
    parser.add_argument(
        "--show-events",
        default=None,
        help="true/false : affiche les Events sur les flèches",
    )
    parser.add_argument(
        "--mode",
        choices=("global", "subfolders", "both"),
        default="both",
        help="Type de génération",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Dossier de sortie des .mmd",
    )
    parser.add_argument(
        "--layout",
        choices=("vertical", "auto"),
        default="vertical",
        help=(
            "vertical: favorise un rendu haut->bas ; "
            "auto: laisse Mermaid choisir librement le placement"
        ),
    )
    parser.add_argument(
        "--renderer",
        choices=("dagre-d3", "dagre-wrapper"),
        default="dagre-d3",
        help="Renderer Mermaid inscrit dans le .mmd",
    )
    parser.add_argument(
        "--rank-spacing",
        type=int,
        default=DEFAULT_RANK_SPACING,
        help=f"Espacement vertical Mermaid en px (défaut: {DEFAULT_RANK_SPACING})",
    )
    parser.add_argument(
        "--node-spacing",
        type=int,
        default=DEFAULT_NODE_SPACING,
        help=f"Espacement horizontal Mermaid en px (défaut: {DEFAULT_NODE_SPACING})",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )

    return parser.parse_args()


def main():
    """
    Point d'entrée principal.

    Étapes :
    1. lire et valider les paramètres ;
    2. charger le JSON ;
    3. construire le modèle mémoire Control-M ;
    4. générer la vue globale et/ou les vues SubFolder demandées.
    """
    args = parse_args()

    # Cas 1 : utilisation en ligne de commande avec fichier JSON.
    if args.json_file:
        json_file = args.json_file

        # Priorité au nouveau paramètre --show-events s'il est fourni.
        if args.show_events is not None:
            show_events = parse_bool(args.show_events)
        elif args.legacy_show_events is not None:
            # Compatibilité avec l'ancien appel : script.py fichier.json false
            show_events = parse_bool(args.legacy_show_events)
        else:
            show_events = False

        mode = args.mode
        layout_mode = args.layout
        renderer = args.renderer
        rank_spacing = args.rank_spacing
        node_spacing = args.node_spacing

    # Cas 2 : aucun argument, on passe en mode interactif.
    else:
        (
            json_file,
            show_events,
            mode,
            layout_mode,
            renderer,
            rank_spacing,
            node_spacing,
        ) = ask_inputs()

    if rank_spacing < 10 or rank_spacing > 200:
        print("Erreur : --rank-spacing doit être compris entre 10 et 200.")
        return 1

    if node_spacing < 10 or node_spacing > 200:
        print("Erreur : --node-spacing doit être compris entre 10 et 200.")
        return 1

    input_path = Path(json_file)

    # --- Validation : le fichier existe et est bien un fichier -------------
    if not input_path.exists():
        print(f"Erreur : fichier introuvable -> {input_path}")
        return 1

    if not input_path.is_file():
        print(f"Erreur : le chemin n'est pas un fichier -> {input_path}")
        return 1

    # Si --output-dir n'est pas fourni, on écrit à côté du JSON.
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Erreur : impossible de créer le dossier de sortie {output_dir} : {exc}")
        return 1

    # --- Validation : le JSON est lisible et bien formé ---------------------
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(
            f"Erreur : JSON invalide dans {input_path} "
            f"(ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}"
        )
        return 1
    except OSError as exc:
        print(f"Erreur : impossible de lire {input_path} : {exc}")
        return 1

    if not isinstance(data, dict) or not data:
        print(
            f"Erreur : {input_path} ne ressemble pas à un export Control-M "
            "(le JSON racine doit être un objet non vide)."
        )
        return 1

    # Construction du modèle mémoire à partir du JSON.
    model = ControlMModel()
    model.walk(data)

    # --- Validation : au moins un Folder racine a été détecté ---------------
    roots = model.roots()
    if not roots:
        print(
            f"Erreur : aucun Folder/SubFolder racine détecté dans {input_path}.\n"
            "Vérifie qu'il s'agit bien d'un export Control-M (une clé de "
            "premier niveau avec \"Type\": \"Folder\")."
        )
        return 1

    # Génération selon le mode demandé.
    if mode in ("global", "both"):
        generate_global(
            model,
            input_path,
            output_dir,
            show_events,
            layout_mode,
            renderer,
            rank_spacing,
            node_spacing,
        )

    if mode in ("subfolders", "both"):
        if not model.subfolders():
            print("Info : aucun SubFolder trouvé, seul le diagramme global a été généré.")
        else:
            generate_subfolders(
                model,
                input_path,
                output_dir,
                show_events,
                layout_mode,
                renderer,
                rank_spacing,
                node_spacing,
            )

    return 0


# Ce bloc permet d'exécuter main() uniquement quand le fichier est lancé
# directement avec python. Si le script est importé depuis un autre script,
# main() ne sera pas exécuté automatiquement.
if __name__ == "__main__":
    sys.exit(main())
