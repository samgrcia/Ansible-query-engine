# TODO — ansible_query

Ordre d'implémentation par phase. Chaque phase dépend des précédentes.

---

## Phase 1 — Mise en place du projet

- [x] `pyproject.toml` — dépendances (`ruamel.yaml`, `ansible-vault`, `click`), metadata, entry point CLI
- [x] Structure de packages vide (`ansible_query/`, `ansible_query/inventory/`, `ansible_query/parser/`, `tests/`)
- [x] `tests/fixtures/` — inventaire de test (nodes.yaml, hostvars/, groupvars/ avec plages et vault)
- [x] `.gitignore`

---

## Phase 2 — Couche inventaire : structures de données

- [x] `ansible_query/inventory/state.py` — `InventoryState` : dict compilé `{ hostname: { var: valeur } }`
- [x] `ansible_query/inventory/bulk.py` — `BulkBuffer` : accumule les modifications `{ fichier_cible: { var: valeur } }` avant écriture

---

## Phase 3 — Couche inventaire : chargement

- [x] `ansible_query/inventory/resolver.py`
  - [x] Parse `nodes.yaml` (hiérarchie plate, groupes à la racine)
  - [x] Expansion des plages (`node[1:3]` → `node1`, `node2`, `node3` ; zéro-padding préservé)
  - [x] Construction du DAG groupes/hosts
  - [x] Tri topologique → chaîne de groupes ordonnée par host (`all → ... → groupe_enfant → host`)
  - [x] Tests unitaires : expansion plages, DAG, tri topologique, groupes frères

- [x] `ansible_query/inventory/store.py`
  - [x] Lecture fichiers `.yaml` avec `ruamel.yaml` (round-trip, commentaires préservés)
  - [x] Lecture fichiers `.vault` : déchiffrement via `ansible-vault` + `ANSIBLE_VAULT_PASSWORD_FILE`
  - [x] Validation au chargement : préfixe `vault_` obligatoire dans `.vault`, pas de doublon `.yaml`/`.vault`
  - [x] Cache fichier (invalidation explicite)
  - [x] Écriture `.yaml` : protocole atomic rename (écriture `.tmp` → `rename()`)
  - [x] Écriture `.vault` : chiffrement + atomic rename
  - [x] Tests unitaires : lecture/écriture, cache, atomic rename, validation vault

---

## Phase 4 — Couche inventaire : compilation

- [x] `ansible_query/inventory/compiler.py`
  - [x] Deep merge des vars par host selon priorité Ansible (`all → groupe_parent → groupe_enfant → host`)
  - [x] Comportement du merge : dicts récursifs, scalaires et listes écrasés
  - [x] Construction de `invalidation_map` au chargement : `{ source → [hosts affectés] }`
  - [x] Recompilation partielle après écriture (via `invalidation_map`)
  - [x] Tests unitaires : deep merge, priorité, invalidation_map, recompilation partielle

---

## Phase 5 — Parser pseudo-SQL

- [x] `ansible_query/parser/ast.py` — dataclasses :
  - [x] `SelectQuery(columns, sources, condition)`
  - [x] `SetQuery(variable, value, condition, encrypt)`
  - [x] `UnsetQuery(variable, source, condition)`
  - [x] `CreateHostQuery(host, groups)`
  - [x] `RemoveHostQuery(host, groups)`
  - [x] `DropHostQuery(host, keep_vars)`
  - [x] `Condition(type, pattern)` — `host =`, `group =`, wildcards

- [x] `ansible_query/parser/lexer.py` — tokenisation : mots-clés, identifiants, littéraux, opérateurs

- [x] `ansible_query/parser/parser.py` — tokens → AST ; erreurs de syntaxe explicites

- [x] Tests unitaires parser : toutes les commandes + cas limites (wildcards, ENCRYPT, KEEP VARS, listes de groupes)

---

## Phase 6 — Moteur de requête

- [x] `ansible_query/engine.py` — `QueryEngine`
  - [x] `__init__(inventory_path)` : charge Resolver → Store → Compiler → InventoryState
  - [x] `execute(query: str)` : parse → dispatch → résultat
  - [x] Handlers : SELECT, SET, SET ENCRYPT, UNSET, CREATE HOST, REMOVE HOST, DROP HOST
  - [x] `execute_bulk(queries: list[str])` : phase 1 (validation mémoire + BulkBuffer) → phase 2 (écriture atomique) → recompilation
  - [x] Gestion vault en bulk : mot de passe lu une seule fois à la première commande ENCRYPT
  - [x] Tests intégration engine : tous les handlers, bulk atomicité, rollback sur erreur

---

## Phase 7 — CLI

- [x] `ansible_query/cli.py` — point d'entrée `ansible-query`
  - [x] Argument positionnel : la requête SQL
  - [x] `--inventory` : chemin de l'inventaire (défaut : `./inventory`)
  - [x] `--output` : `json` (défaut) | `yaml` | `table`
  - [x] `--dry-run` : affiche ce qui serait écrit sans toucher les fichiers
  - [x] Tests CLI : smoke tests sur les commandes principales

---

## Phase 8 — Finitions

- [x] `ansible_query/__init__.py` — export public : `InventoryQuery`
- [x] Vérification compatibilité Python 3.12+
- [x] README.md : installation, usage CLI et bibliothèque

---

## Légende

| Symbole | Statut |
|---|---|
| `[ ]` | À faire |
| `[~]` | En cours |
| `[x]` | Terminé |
