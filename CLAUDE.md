# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`ansible_query` — Python 3.12+ library and CLI (`ansible-query`) providing a pseudo-SQL interface to Ansible inventories. See `architecture.md` for the full spec and `TODO.md` for the implementation roadmap.

## Commands

```bash
# Install in dev mode
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/test_resolver.py

# Run a single test
pytest tests/test_resolver.py::test_range_expansion

# Type check
mypy ansible_query

# Lint
ruff check ansible_query tests
```

## Architecture

```
ansible_query/
  inventory/
    resolver.py    # Parse nodes.yaml → expand ranges → build DAG → topo sort → group chain per host
    store.py       # Read/write YAML (ruamel.yaml round-trip) + vault decrypt/encrypt; file cache
    compiler.py    # Deep-merge vars per host (Ansible priority); invalidation_map for partial recompile
    state.py       # InventoryState: compiled dict { hostname: { var: value } }
    bulk.py        # BulkBuffer: accumulates { file → changes } before atomic write
  parser/
    ast.py         # Query dataclasses (SelectQuery, SetQuery, UnsetQuery, CreateHostQuery, …)
    lexer.py       # Tokeniser
    parser.py      # Tokens → AST
  engine.py        # QueryEngine: orchestrates all layers; execute() and execute_bulk()
  cli.py           # click entry-point `ansible-query`
```

### Load sequence (eager, mirrors Ansible)

1. **Resolver** — parses `nodes.yaml`, expands ranges (`node[1:3]` → node1..node3), builds directed group/host graph, produces an ordered group chain per host via topological sort.
2. **Store** — loads all `hostvars/` and `groupvars/` YAML/vault files into memory cache; vault files are transparently decrypted using `ANSIBLE_VAULT_PASSWORD_FILE`.
3. **Compiler** — merges vars per host in Ansible priority order (`all → parent group → child group → host`). Dicts are recursively merged; scalars and lists are overwritten (not concatenated). Builds `invalidation_map` for partial recompile.
4. **QueryEngine** — operates on the in-memory `InventoryState`.

### Inventory format

```
inventory/
  nodes.yaml          # groups at root (no `all` wrapper), Ansible range syntax supported
  hostvars/<host>/<host>.yaml
  hostvars/<host>/<host>.vault
  groupvars/<group>/<group>.yaml
  groupvars/<group>/<group>.vault
```

Vault files enforce: all keys must carry `vault_` prefix; a key cannot appear in both `.yaml` and `.vault` for the same host/group — both violations raise at load time.

### Writes

`SET` always targets the explicitly specified source file (`hostvars/<host>/<host>.yaml` or `groupvars/<group>/<group>.yaml`). After each write: Store invalidates cache → Compiler recompiles only the affected hosts via `invalidation_map`.

Atomic write protocol: write to `<file>.tmp` → `os.rename()` → clean up `.tmp` on any failure.

`SET ... ENCRYPT` automatically prepends `vault_` to the var name and writes to the `.vault` file. `ANSIBLE_VAULT_PASSWORD_FILE` must be set or the command fails immediately.

`UNSET` routes to `.vault` if the variable has the `vault_` prefix, otherwise to `.yaml`.

### Bulk execution (`execute_bulk`)

Phase 1: parse all commands, apply each to in-memory `InventoryState`, accumulate changes in `BulkBuffer` — abort and discard buffer entirely on the first failure.  
Phase 2 (only if phase 1 succeeds): write all modified files atomically, then recompile affected hosts once.

## Key dependencies

| Package | Role |
|---|---|
| `ruamel.yaml` | Round-trip YAML (preserves comments, key order, indentation) |
| `ansible-vault` | Vault encrypt/decrypt |
| `click` | CLI |
