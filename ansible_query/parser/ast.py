from dataclasses import dataclass
from typing import Any


@dataclass
class Condition:
    kind: str     # "host" | "group"
    pattern: str  # may contain * wildcard


@dataclass
class SelectQuery:
    columns: list[str]   # ["*"]  or  ["var1", "var2", ...]
    sources: list[str]   # subset of ["hostvars", "groupvars"]
    condition: Condition


@dataclass
class SetQuery:
    variable: str
    value: Any           # str | int
    condition: Condition
    encrypt: bool


@dataclass
class UnsetQuery:
    variable: str
    source: str          # "hostvars" | "groupvars"
    condition: Condition


@dataclass
class CreateHostQuery:
    host: str
    groups: list[str]    # empty list → add to ungrouped


@dataclass
class RemoveHostQuery:
    host: str
    groups: list[str]


@dataclass
class DropHostQuery:
    host: str
    keep_vars: bool


@dataclass
class ShowHostsQuery:
    pattern: str   # fnmatch pattern; "*" means all hosts


@dataclass
class ShowGroupsQuery:
    pattern: str   # fnmatch pattern; "*" means all groups


Query = (
    SelectQuery | SetQuery | UnsetQuery
    | CreateHostQuery | RemoveHostQuery | DropHostQuery
    | ShowHostsQuery | ShowGroupsQuery
)
