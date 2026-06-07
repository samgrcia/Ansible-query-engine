import io
import json
import sys
from pathlib import Path
from typing import Any

import click
from ruamel.yaml import YAML

from ansible_query.engine import QueryEngine, QueryError
from ansible_query.parser.ast import (
    CreateHostQuery,
    DropHostQuery,
    RemoveHostQuery,
    SelectQuery,
    SetQuery,
    ShowGroupsQuery,
    ShowHostsQuery,
    UnsetQuery,
)
from ansible_query.parser.parser import ParseError, parse

_READ_ONLY = (SelectQuery, ShowHostsQuery, ShowGroupsQuery)


@click.command()
@click.argument("query")
@click.option(
    "--inventory", "-i",
    default="./inventory",
    show_default=True,
    help="Path to the Ansible inventory directory.",
)
@click.option(
    "--output", "-o",
    type=click.Choice(["json", "yaml", "table"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format for SELECT results.",
)
@click.option(
    "--dry-run", "-n",
    is_flag=True,
    default=False,
    help="Show what would be changed without writing any files.",
)
def cli(query: str, inventory: str, output: str, dry_run: bool) -> None:
    """Execute a pseudo-SQL query against an Ansible inventory."""
    inv_path = Path(inventory)
    if not inv_path.exists():
        click.echo(f"Error: inventory not found: {inv_path}", err=True)
        sys.exit(1)

    try:
        ast = parse(query)
    except ParseError as e:
        click.echo(f"Parse error: {e}", err=True)
        sys.exit(1)

    try:
        engine = QueryEngine(inv_path)
    except Exception as e:
        click.echo(f"Error loading inventory: {e}", err=True)
        sys.exit(1)

    if dry_run and not isinstance(ast, _READ_ONLY):
        _dry_run(ast, engine, inv_path)
        return

    try:
        result = engine.execute(query)
    except QueryError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if isinstance(ast, _READ_ONLY):
        _print_result(result, output)
    else:
        click.echo("Done.")


# ── dry-run ────────────────────────────────────────────────────────────────────

def _dry_run(ast: Any, engine: QueryEngine, inv_path: Path) -> None:
    click.echo("Dry run — no files will be written.\n")

    if isinstance(ast, SetQuery):
        var_name = (
            ast.variable
            if not ast.encrypt or ast.variable.startswith("vault_")
            else f"vault_{ast.variable}"
        )
        suffix = ".vault" if ast.encrypt else ".yaml"
        if ast.condition.kind == "host":
            hosts = engine.match_hosts(ast.condition.pattern)
            if not hosts:
                click.echo(f"No hosts match: {ast.condition.pattern!r}")
                return
            click.echo(f"Would SET {var_name} = {ast.value!r} on:")
            for host in hosts:
                click.echo(f"  {host}  ({inv_path}/hostvars/{host}/{host}{suffix})")
        else:
            groups = engine.match_groups(ast.condition.pattern)
            if not groups:
                click.echo(f"No groups match: {ast.condition.pattern!r}")
                return
            click.echo(f"Would SET {var_name} = {ast.value!r} on:")
            for group in groups:
                click.echo(f"  {group}  ({inv_path}/groupvars/{group}/{group}{suffix})")

    elif isinstance(ast, UnsetQuery):
        suffix = ".vault" if ast.variable.startswith("vault_") else ".yaml"
        if ast.condition.kind == "host":
            hosts = engine.match_hosts(ast.condition.pattern)
            if not hosts:
                click.echo(f"No hosts match: {ast.condition.pattern!r}")
                return
            click.echo(f"Would UNSET {ast.variable!r} from {ast.source} of:")
            for host in hosts:
                click.echo(f"  {host}  ({inv_path}/{ast.source}/{host}/{host}{suffix})")
        else:
            groups = engine.match_groups(ast.condition.pattern)
            if not groups:
                click.echo(f"No groups match: {ast.condition.pattern!r}")
                return
            click.echo(f"Would UNSET {ast.variable!r} from {ast.source} of:")
            for group in groups:
                click.echo(f"  {group}  ({inv_path}/{ast.source}/{group}/{group}{suffix})")

    elif isinstance(ast, CreateHostQuery):
        target_groups = ast.groups if ast.groups else ["ungrouped"]
        click.echo(f"Would CREATE HOST {ast.host!r}")
        click.echo(f"  Groups : {', '.join(target_groups)}")
        click.echo(f"  File   : {inv_path}/nodes.yaml")

    elif isinstance(ast, RemoveHostQuery):
        click.echo(f"Would REMOVE HOST {ast.host!r} from: {', '.join(ast.groups)}")
        click.echo(f"  File   : {inv_path}/nodes.yaml")

    elif isinstance(ast, DropHostQuery):
        action = "keep hostvars files" if ast.keep_vars else "delete hostvars files"
        click.echo(f"Would DROP HOST {ast.host!r} ({action})")
        click.echo(f"  File   : {inv_path}/nodes.yaml")
        if not ast.keep_vars:
            click.echo(f"  Remove : {inv_path}/hostvars/{ast.host}/")


# ── output formatting ─────────────────────────────────────────────────────────

def _print_result(result: Any, output: str) -> None:
    if output == "json":
        click.echo(json.dumps(result, indent=2, default=str))
    elif output == "yaml":
        buf = io.StringIO()
        yaml = YAML()
        yaml.dump(result, buf)
        click.echo(buf.getvalue(), nl=False)
    else:
        _print_table(result)


def _print_table(data: Any) -> None:
    if not isinstance(data, dict) or not data:
        click.echo(str(data) if data != {} else "(empty)")
        return

    first_val = next(iter(data.values()))
    if isinstance(first_val, list):
        # SHOW HOSTS / SHOW GROUPS: entity → list of members
        headers = ["", "MEMBERS"]
        rows = [[str(k), ", ".join(str(x) for x in v)] for k, v in data.items()]
    elif isinstance(first_val, dict):
        # Multi-entity SELECT: rows = hosts/groups, columns = variable names
        entities = list(data.keys())
        col_keys: list[str] = []
        seen: set[str] = set()
        for v in data.values():
            if isinstance(v, dict):
                for k in v:
                    sk = str(k)
                    if sk not in seen:
                        col_keys.append(sk)
                        seen.add(sk)
        headers = [""] + col_keys
        rows = [
            [entity] + [str(data[entity].get(k, "")) for k in col_keys]
            for entity in entities
        ]
    else:
        headers = ["KEY", "VALUE"]
        rows = [[str(k), str(v)] for k, v in data.items()]

    _render_table(headers, rows)


def _render_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    sep = "  "
    click.echo(sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    click.echo(sep.join("-" * w for w in widths))
    for row in rows:
        click.echo(sep.join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
