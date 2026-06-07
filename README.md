# Ansible query engine

Query and modify Ansible inventories using a pseudo-SQL language — usable as a **CLI tool** or as a **Python library**.

```bash
ansible-query 'SELECT * FROM hostvars WHERE host = "web1"'
ansible-query 'SET env = "production" WHERE group = "webservers"'
ansible-query 'DROP HOST decommissioned-node'
```

## Installation

```bash
pip install ansible-query
```

To use vault-encrypted variables, set the path to your vault password file:

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.vault_pass
```

## Inventory format

`ansible-query` works with inventories structured as follows:

```
inventory/
  nodes.yaml                    # groups and hosts (no top-level "all" wrapper)
  hostvars/
    <hostname>/
      <hostname>.yaml           # plain variables
      <hostname>.vault          # encrypted variables (vault_ prefix required)
  groupvars/
    <groupname>/
      <groupname>.yaml
      <groupname>.vault
```

`nodes.yaml` example — groups at the root, Ansible range syntax supported:

```yaml
app_servers:
  children:
    webservers:
    dbservers:
webservers:
  hosts:
    node[1:3]:       # expands to node1, node2, node3
europe:
  hosts:
    node1:
    node2:
```

Two constraints on vault files: every key must carry a `vault_` prefix, and the same key cannot appear in both `.yaml` and `.vault` for the same host or group.

## Command reference

### SELECT

```sql
-- All variables for a host (hostvars only)
SELECT * FROM hostvars WHERE host = "node1"

-- Single variable — returns a scalar
SELECT env FROM hostvars WHERE host = "node1"

-- Compiled view: group vars merged with host vars (Ansible priority order)
SELECT * FROM hostvars, groupvars WHERE host = "node1"

-- Group variables
SELECT * FROM groupvars WHERE group = "webservers"

-- Wildcard — returns a dict keyed by host
SELECT ansible_host FROM hostvars WHERE host = "node*"
```

### SET

```sql
-- Set a variable on a host
SET env = "production" WHERE host = "node1"

-- Set on all matching hosts
SET http_port = 443 WHERE host = "web*"

-- Set on a group
SET deploy_user = "ansible" WHERE group = "webservers"

-- Encrypt (writes to .vault, prepends vault_ prefix automatically)
SET db_password = "secret" WHERE host = "node1" ENCRYPT
```

### UNSET

```sql
UNSET env FROM hostvars WHERE host = "node1"
UNSET http_port FROM groupvars WHERE group = "webservers"
UNSET vault_db_password FROM hostvars WHERE host = "node1"
```

### CREATE HOST

```sql
CREATE HOST new-server
CREATE HOST new-server IN GROUPS webservers, europe
```

### REMOVE HOST

```sql
-- Remove a host from specific groups (host must be listed by its exact name, not via a range)
REMOVE HOST node1 FROM GROUPS europe
```

### DROP HOST

```sql
-- Remove from all groups and delete hostvars files
DROP HOST old-server

-- Remove from all groups but keep hostvars files
DROP HOST old-server KEEP VARS
```

## CLI

```
Usage: ansible-query [OPTIONS] QUERY

  Execute a pseudo-SQL query against an Ansible inventory.

Options:
  -i, --inventory PATH           Path to the inventory directory.  [default: ./inventory]
  -o, --output [json|yaml|table] Output format for SELECT results. [default: json]
  -n, --dry-run                  Show what would be changed without writing any files.
```

### Output formats

```bash
# JSON (default)
ansible-query 'SELECT * FROM hostvars WHERE host = "node1"'

# YAML
ansible-query 'SELECT * FROM hostvars WHERE host = "node1"' --output yaml

# Table
ansible-query 'SELECT env, http_port FROM hostvars WHERE host = "node*"' --output table
```

### Dry run

```bash
ansible-query 'SET env = "staging" WHERE host = "node*"' --dry-run

# Dry run — no files will be written.
#
# Would SET env = 'staging' on:
#   node1  (inventory/hostvars/node1/node1.yaml)
#   node2  (inventory/hostvars/node2/node2.yaml)
#   node3  (inventory/hostvars/node3/node3.yaml)
```

## Python library

`ansible-query` can also be embedded directly in Python code:

```python
from ansible_query.engine import QueryEngine

engine = QueryEngine("./inventory")

# Execute a single query
result = engine.execute('SELECT * FROM hostvars, groupvars WHERE host = "node1"')
print(result["env"])

# Bulk execution — all writes are atomic: either all succeed or nothing is written
engine.execute_bulk([
    'SET env = "production" WHERE host = "node1"',
    'SET env = "production" WHERE host = "node2"',
    'UNSET old_var FROM hostvars WHERE host = "node1"',
])
```

`execute_bulk` runs in two phases: Phase 1 validates and stages every change in memory; Phase 2 writes all modified files atomically (`.tmp` → rename). A failure in Phase 1 discards the entire batch with no disk writes.

## Known limitations

- `REMOVE HOST` and `DROP HOST` only work for hosts listed by their exact name in `nodes.yaml`. Hosts included via a range pattern (e.g. `node[1:3]`) must be removed manually.
- `execute_bulk` does not support `REMOVE HOST` or `DROP HOST`.
- `CREATE HOST` is supported in `execute_bulk` and is the recommended way to create many hosts at once (all entries are written to `nodes.yaml` in a single atomic pass).
