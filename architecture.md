# ansible_query

Bibliothèque Python avec interface CLI permettant d'interroger et modifier un inventaire Ansible via un pseudo-langage SQL.

---

## Objectif

Fournir une interface de requête lisible et expressive sur les fichiers d'inventaire Ansible (hosts, groupes, variables), en respectant fidèlement la sémantique de résolution des variables d'Ansible.

---

## Structure de l'inventaire cible

```
inventory/
  nodes.yaml                          # hosts et groupes au format Ansible natif
  hostvars/
    <hostname>/
      <hostname>.yaml                 # variables en clair
      <hostname>.vault                # variables chiffrées (préfixe vault_)
  groupvars/
    <groupname>/
      <groupname>.yaml                # variables en clair
      <groupname>.vault               # variables chiffrées (préfixe vault_)
```

`nodes.yaml` suit le format Ansible YAML sans wrapper `all` — les groupes sont définis à la racine du fichier :

```yaml
webservers:
  hosts:
    node1:
    node2:
dbservers:
  hosts:
    node3:
```

Les nœuds peuvent utiliser la syntaxe de plage Ansible (`node[1:3]` → `node1`, `node2`, `node3` ; `web[01:05]` → `web01` … `web05` avec zéro-padding préservé). L'expansion est effectuée par le Resolver au chargement, avant la construction du DAG.

---

## Langage de requête

Interface pseudo-SQL. Exemples :

```sql
-- Lecture simple
SELECT ma_variable FROM hostvars WHERE host = "node1"

-- Jointure avec priorité Ansible (groupvars < hostvars)
SELECT ma_variable FROM hostvars, groupvars WHERE host = "node1"

-- Lecture depuis groupvars
SELECT * FROM groupvars WHERE group = "nginx"

-- Wildcard
SELECT * FROM hostvars WHERE host = "node*"

-- Écriture en clair (toujours sur une source explicite)
SET ma_variable = "valeur" WHERE host = "node1"
SET ma_variable = "valeur" WHERE group = "nginx"

-- Écriture chiffrée dans le vault
-- Le préfixe vault_ est ajouté automatiquement au nom de la variable
-- La valeur est chiffrée via ansible-vault avec ANSIBLE_VAULT_PASSWORD_FILE
SET root_password = "secret" WHERE host = "node1" ENCRYPT
SET db_password = "secret" WHERE group = "dbservers" ENCRYPT

-- Suppression de variable (erreur si la variable n'existe pas)
UNSET ma_variable FROM hostvars WHERE host = "node1"
UNSET ma_variable FROM groupvars WHERE group = "nginx"

-- Création d'un host (sans groupe → ajout à ungrouped)
CREATE HOST node1
CREATE HOST node1 IN GROUPS webservers
CREATE HOST node1 IN GROUPS webservers, europe

-- Suppression d'un host d'un ou plusieurs groupes (tombe dans ungrouped si c'était son seul groupe)
REMOVE HOST node1 FROM GROUPS webservers
REMOVE HOST node1 FROM GROUPS webservers, europe

-- Suppression d'un host (nodes.yaml + hostvars/<host>/)
DROP HOST node1
DROP HOST node1 KEEP VARS
```

Les jointures `hostvars, groupvars` appliquent un merge avec priorité identique à Ansible : `all < groupe_parent < groupe_enfant < host`.

### Format de sortie des SELECT

**CLI** : JSON par défaut. Options disponibles : `--output yaml`, `--output table`.

**Bibliothèque** : objet Python natif — `dict` pour un résultat multi-clés, `list` si la valeur est une liste, scalaire (`str`, `int`, `bool`) sinon. `None` si la variable est absente.

---

## Architecture

### Couches

```
ansible_query/
  inventory/
    resolver.py    # parse nodes.yaml, construit le DAG hosts/groupes, tri topologique
    store.py       # lecture/écriture fichiers YAML et déchiffrement/chiffrement vault, cache fichier
    compiler.py    # merge des vars par host selon priorité Ansible
    state.py       # InventoryState : dict compilé { hostname: { var: valeur } }
    bulk.py        # BulkBuffer : accumule les modifications en mémoire avant écriture
  parser/
    lexer.py       # tokenisation du pseudo-SQL
    ast.py         # dataclasses QueryAST, Condition, etc.
  engine.py        # Query Engine : orchestre resolver, store, compiler
  cli.py           # point d'entrée CLI
```

### Séquence de chargement (eager, comme Ansible)

1. **Resolver** — parse `nodes.yaml`, expand les plages (`node[1:3]`), construit le graphe orienté (DAG), calcule pour chaque host la chaîne de groupes ordonnée par priorité via tri topologique.
2. **Store** — charge tous les fichiers `hostvars/` et `groupvars/` en mémoire avec cache.
3. **Compiler** — pour chaque host, merge les vars dans l'ordre : `all → ... → groupe_enfant → host`. Produit l'`InventoryState`.
4. **Query Engine** — travaille uniquement sur l'`InventoryState` en mémoire.

### Deep merge

Le merge reproduit le comportement Ansible :
- Les dicts sont mergés récursivement.
- Les scalaires et les listes sont **écrasés** (pas concaténés).

---

## Mode bulk

`execute_bulk()` accepte une liste de commandes et les applique en deux phases :

**Phase 1 — Validation et application en mémoire**
Chaque commande est parsée et exécutée sur l'`InventoryState` en mémoire, sans aucune écriture disque. Un buffer de modifications (`BulkBuffer`) accumule les changements par fichier cible. Si une commande échoue (variable inexistante, host inconnu, contrainte vault violée, etc.), le buffer est intégralement abandonné et une erreur est levée — aucun fichier n'est touché.

**Phase 2 — Écriture atomique**
Si toutes les commandes ont réussi, le buffer est vidé en une seule passe via le protocole suivant :
1. Chaque fichier à modifier est d'abord écrit dans un fichier temporaire adjacent (`<fichier>.tmp`).
2. Une fois **tous** les `.tmp` écrits avec succès, chaque fichier original est remplacé par son `.tmp` via un `rename()` atomique (atomique au niveau filesystem sur le même volume).
3. En cas d'échec à l'étape 1 ou 2, les `.tmp` résiduels sont supprimés — les originaux restent intacts.

La recompilation partielle des hosts affectés est effectuée une seule fois à la fin, pas après chaque commande.

**Gestion du vault en mode bulk**
Le mot de passe vault est lu depuis `ANSIBLE_VAULT_PASSWORD_FILE` à la première occurrence d'une commande `ENCRYPT` dans le bloc, puis réutilisé pour toutes les suivantes. Si la variable d'environnement n'est pas définie, le bloc est invalidé dès cette première occurrence.

```
execute_bulk(commands)
│
├── 1. Parse toutes les commandes → erreur immédiate si syntaxe invalide
├── 2. Pour chaque commande :
│     ├── Applique sur InventoryState (mémoire)
│     ├── Accumule dans BulkBuffer { fichier → modifications }
│     └── Erreur → abandon total du BulkBuffer
└── 3. Succès → vide le BulkBuffer → écrit les fichiers modifiés → recompile
```

---

## Gestion des écritures et cohérence

### Règle fondamentale

Un `SET` écrit toujours dans le **fichier source explicitement ciblé** (`hostvars/<host>/<host>.yaml` ou `groupvars/<group>/<group>.yaml`). La décompilation inverse est volontairement absente pour éviter toute ambiguïté.

### Recompilation partielle après écriture

Pour garantir la cohérence lors de `SET` successifs dans un même script, le Compiler maintient une **map d'invalidation** construite au chargement :

```python
invalidation_map = {
    "groupvars/nginx":     ["node1", "node2"],
    "groupvars/all":       ["node1", "node2", "node3", ...],
    "hostvars/node1":      ["node1"],
}
```

Après chaque `SET` :
1. Le Store écrit le fichier et invalide son cache.
2. Le Compiler recompile uniquement les hosts affectés (via `invalidation_map`).
3. L'`InventoryState` est toujours cohérent pour la requête suivante.

Le coût est proportionnel à l'impact réel de l'écriture, pas à la taille totale de l'inventaire.

---

## Dépendances techniques

| Librairie | Usage | Justification |
|---|---|---|
| `ruamel.yaml` | Lecture et écriture de tous les fichiers YAML | Préserve les commentaires, l'ordre des clés et l'indentation lors des écritures (round-trip). PyYAML détruit les commentaires et réordonne les clés — incompatible avec des fichiers maintenus par des humains. |
| `ansible-vault` | Chiffrement et déchiffrement des fichiers `.vault` | Bibliothèque Python officielle pour la gestion des vaults Ansible. Le mot de passe est lu depuis le fichier pointé par `ANSIBLE_VAULT_PASSWORD_FILE`. |

---

## Performance

À l'échelle cible (quelques milliers de hosts, ~30 groupes), les performances ne sont pas un problème architectural :

- L'inventaire complet tient en un seul `nodes.yaml` + ~30 fichiers groupvars + un sous-ensemble de fichiers hostvars — tout tient en mémoire sans effort.
- Le tri topologique du DAG est effectué une seule fois au chargement.
- Les wildcards (`WHERE host = "node*"`) s'appuient sur un filtre linéaire sur les clés du dict compilé, imperceptible à ce volume.

Le seul point d'attention est l'écriture YAML, géré par `ruamel.yaml`.

---

## Comportements des commandes structurelles

### CREATE HOST

1. Ajoute le host dans `nodes.yaml` sous `ungrouped` (si pas de groupe) ou sous le(s) groupe(s) spécifiés.
2. Si un groupe spécifié n'existe pas, il est créé automatiquement dans `nodes.yaml`.
3. Ne crée pas de répertoire `hostvars/<host>/` — il sera créé au premier `SET`.
4. Déclenche une recompilation partielle des hosts affectés par les groupes concernés.

### REMOVE HOST FROM GROUP

1. Retire le host du groupe dans `nodes.yaml`.
2. Si c'était son seul groupe explicite, le host est automatiquement placé sous `ungrouped`.
3. Ne touche pas au répertoire `hostvars/<host>/`.
4. Déclenche une recompilation du host (sa chaîne de groupes change).

### DROP HOST

1. Retire le host de `nodes.yaml` (tous ses groupes).
2. Supprime le répertoire `hostvars/<host>/` et son contenu (`.yaml` et `.vault`).
3. Avec `KEEP VARS` : supprime uniquement l'entrée dans `nodes.yaml`, conserve le répertoire hostvars.
4. Invalide et supprime l'entrée du host dans l'`InventoryState`.

### SET ENCRYPT

1. Ajoute automatiquement le préfixe `vault_` au nom de la variable.
2. Lit le mot de passe vault depuis le fichier pointé par `ANSIBLE_VAULT_PASSWORD_FILE` — erreur explicite si la variable d'environnement n'est pas définie.
3. Chiffre la valeur via `ansible-vault` et écrit dans `hostvars/<host>/<host>.vault` ou `groupvars/<group>/<group>.vault`.
4. Déclenche une recompilation partielle des hosts affectés.

### Règles des fichiers .vault

1. Toute variable présente dans un fichier `.vault` doit obligatoirement porter le préfixe `vault_` — erreur explicite au chargement si ce n'est pas le cas.
2. Une même variable ne peut pas apparaître à la fois dans le `.yaml` et le `.vault` du même host ou groupe — erreur explicite au chargement.
3. Les fichiers `.vault` sont déchiffrés à la volée au chargement du Store, de manière transparente pour le Query Engine et le Compiler.

---

### UNSET

1. Supprime la clé du fichier source ciblé. Le fichier cible (`.yaml` ou `.vault`) est déterminé par le nom de la variable : si elle porte le préfixe `vault_`, la suppression s'effectue dans le `.vault` ; sinon dans le `.yaml`.
2. Erreur explicite si la variable n'existe pas dans la source ciblée.
3. Déclenche une recompilation partielle des hosts affectés.

---

## Points de vigilance

| Sujet | Décision |
|---|---|
| Jointure hostvars + groupvars | Merge avec priorité Ansible (pas un UNION) |
| Listes lors du merge | Écrasement (comportement Ansible natif) |
| Wildcards en écriture | Flag `--dry-run` recommandé + confirmation explicite |
| Groupes multiples / frères | Résolution par tri topologique ; `ansible_group_priority` hors scope v1 |
| Host inexistant lors d'un SET | Erreur explicite (pas de création implicite dans `nodes.yaml`) |
| Recompilation | Partielle et immédiate après chaque écriture |
| CREATE HOST sans groupe | Ajout sous `ungrouped` |
| CREATE HOST avec groupe inexistant | Création automatique du groupe dans `nodes.yaml` |
| REMOVE HOST dernier groupe | Bascule automatiquement sous `ungrouped` |
| DROP HOST | Supprime entrée `nodes.yaml` + répertoire `hostvars/<host>/` |
| DROP HOST KEEP VARS | Supprime uniquement l'entrée `nodes.yaml`, conserve `hostvars/<host>/` |
| UNSET variable inexistante | Erreur explicite |
| UNSET variable `vault_` | Cible automatiquement le `.vault` du host/groupe |
| SET ... ENCRYPT | Préfixe `vault_` ajouté automatiquement, écrit dans le `.vault` |
| Variable vault_ sans ENCRYPT | Erreur — les variables `vault_` ne peuvent être écrites que via ENCRYPT |
| Variable sans préfixe vault_ dans un .vault | Erreur explicite au chargement |
| Même variable dans .yaml et .vault | Erreur explicite au chargement |
| ANSIBLE_VAULT_PASSWORD_FILE non définie | Erreur explicite à l'exécution de ENCRYPT |
| Erreur dans un bloc bulk | Abandon total du BulkBuffer, aucun fichier écrit |
| Mot de passe vault en mode bulk | Lu une seule fois à la première commande ENCRYPT du bloc |

---

## Usage envisagé

```python
# En tant que bibliothèque
from ansible_query import InventoryQuery

iq = InventoryQuery("inventory/")
iq.execute('SET env = "production" WHERE group = "webservers"')
result = iq.execute('SELECT env FROM hostvars, groupvars WHERE host = "node1"')

# Mode bulk : toutes les commandes sont appliquées en mémoire,
# puis écrites d'un coup dans les fichiers concernés.
# Si une commande échoue, aucune écriture n'est effectuée.
iq.execute_bulk([
    'SET env = "production" WHERE host = "node1"',
    'SET env = "production" WHERE host = "node2"',
    'SET root_password = "secret" WHERE host = "node1" ENCRYPT',
    'UNSET old_var FROM hostvars WHERE host = "node3"',
])
```

```bash
# En tant que CLI
ansible-query 'SELECT * FROM hostvars WHERE host = "node*"'
ansible-query 'SET env = "staging" WHERE group = "nginx"' --dry-run
ansible-query 'CREATE HOST node6 IN GROUPS webservers, europe'
ansible-query 'REMOVE HOST node1 FROM GROUPS webservers'
ansible-query 'DROP HOST node1'
ansible-query 'DROP HOST node1 KEEP VARS'
ansible-query 'UNSET env FROM hostvars WHERE host = "node1"'
ansible-query 'SET root_password = "secret" WHERE host = "node1" ENCRYPT'
```
