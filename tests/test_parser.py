import pytest

from ansible_query.parser.ast import (
    AddHostQuery,
    Condition,
    CreateHostQuery,
    DropHostQuery,
    RemoveHostQuery,
    SelectQuery,
    SetQuery,
    ShowGroupsQuery,
    ShowHostsQuery,
    UnsetQuery,
)
from ansible_query.parser.lexer import LexError, TokenType, tokenize
from ansible_query.parser.parser import ParseError, parse

# ── lexer ─────────────────────────────────────────────────────────────────────

def test_lex_keywords_case_insensitive() -> None:
    tokens = tokenize("SELECT FROM WHERE")
    types = [t.type for t in tokens[:-1]]  # exclude EOF
    assert types == [TokenType.SELECT, TokenType.FROM, TokenType.WHERE]


def test_lex_string_literal() -> None:
    tokens = tokenize('"hello world"')
    assert tokens[0].type == TokenType.STRING
    assert tokens[0].value == "hello world"


def test_lex_integer() -> None:
    tokens = tokenize("42")
    assert tokens[0].type == TokenType.INTEGER
    assert tokens[0].value == 42


def test_lex_identifier_with_hyphen() -> None:
    tokens = tokenize("web-server")
    assert tokens[0].type == TokenType.IDENTIFIER
    assert tokens[0].value == "web-server"


def test_lex_wildcard_in_string() -> None:
    tokens = tokenize('"node*"')
    assert tokens[0].value == "node*"


def test_lex_unterminated_string_raises() -> None:
    with pytest.raises(LexError, match="Unterminated"):
        tokenize('"unclosed')


def test_lex_unexpected_char_raises() -> None:
    with pytest.raises(LexError, match="Unexpected"):
        tokenize("SELECT @ FROM")


def test_lex_hostvars_groupvars() -> None:
    tokens = tokenize("hostvars groupvars")
    assert tokens[0].type == TokenType.HOSTVARS
    assert tokens[1].type == TokenType.GROUPVARS


# ── SELECT ────────────────────────────────────────────────────────────────────

def test_select_star_hostvars_host() -> None:
    q = parse('SELECT * FROM hostvars WHERE host = "node1"')
    assert isinstance(q, SelectQuery)
    assert q.columns == ["*"]
    assert q.sources == ["hostvars"]
    assert q.condition == Condition("host", "node1")


def test_select_specific_column() -> None:
    q = parse('SELECT env FROM hostvars WHERE host = "node1"')
    assert isinstance(q, SelectQuery)
    assert q.columns == ["env"]


def test_select_multiple_columns() -> None:
    q = parse('SELECT env, http_port FROM hostvars WHERE host = "node1"')
    assert isinstance(q, SelectQuery)
    assert q.columns == ["env", "http_port"]


def test_select_multiple_sources() -> None:
    q = parse('SELECT * FROM hostvars, groupvars WHERE host = "node1"')
    assert isinstance(q, SelectQuery)
    assert q.sources == ["hostvars", "groupvars"]


def test_select_groupvars_group_condition() -> None:
    q = parse('SELECT * FROM groupvars WHERE group = "nginx"')
    assert isinstance(q, SelectQuery)
    assert q.sources == ["groupvars"]
    assert q.condition == Condition("group", "nginx")


def test_select_wildcard_pattern() -> None:
    q = parse('SELECT * FROM hostvars WHERE host = "node*"')
    assert isinstance(q, SelectQuery)
    assert q.condition.pattern == "node*"


# ── SET ───────────────────────────────────────────────────────────────────────

def test_set_string_host() -> None:
    q = parse('SET env = "production" WHERE host = "node1"')
    assert isinstance(q, SetQuery)
    assert q.variable == "env"
    assert q.value == "production"
    assert q.condition == Condition("host", "node1")
    assert q.encrypt is False


def test_set_integer_value() -> None:
    q = parse('SET http_port = 8080 WHERE group = "webservers"')
    assert isinstance(q, SetQuery)
    assert q.value == 8080
    assert q.condition == Condition("group", "webservers")


def test_set_encrypt_flag() -> None:
    q = parse('SET root_password = "secret" WHERE host = "node1" ENCRYPT')
    assert isinstance(q, SetQuery)
    assert q.variable == "root_password"
    assert q.value == "secret"
    assert q.encrypt is True


def test_set_encrypt_group() -> None:
    q = parse('SET db_password = "secret" WHERE group = "dbservers" ENCRYPT')
    assert isinstance(q, SetQuery)
    assert q.condition == Condition("group", "dbservers")
    assert q.encrypt is True


# ── UNSET ─────────────────────────────────────────────────────────────────────

def test_unset_hostvars() -> None:
    q = parse('UNSET env FROM hostvars WHERE host = "node1"')
    assert isinstance(q, UnsetQuery)
    assert q.variable == "env"
    assert q.source == "hostvars"
    assert q.condition == Condition("host", "node1")


def test_unset_groupvars() -> None:
    q = parse('UNSET env FROM groupvars WHERE group = "nginx"')
    assert isinstance(q, UnsetQuery)
    assert q.source == "groupvars"
    assert q.condition == Condition("group", "nginx")


def test_unset_vault_variable() -> None:
    q = parse('UNSET vault_root_password FROM hostvars WHERE host = "node1"')
    assert isinstance(q, UnsetQuery)
    assert q.variable == "vault_root_password"


# ── CREATE HOST ───────────────────────────────────────────────────────────────

def test_create_host_no_groups() -> None:
    q = parse("CREATE HOST node1")
    assert isinstance(q, CreateHostQuery)
    assert q.host == "node1"
    assert q.groups == []


def test_create_host_single_group() -> None:
    q = parse("CREATE HOST node1 IN GROUPS webservers")
    assert isinstance(q, CreateHostQuery)
    assert q.groups == ["webservers"]


def test_create_host_multiple_groups() -> None:
    q = parse("CREATE HOST node1 IN GROUPS webservers, europe")
    assert isinstance(q, CreateHostQuery)
    assert q.groups == ["webservers", "europe"]


def test_create_host_hyphenated_name() -> None:
    q = parse("CREATE HOST web-server-01 IN GROUPS webservers")
    assert isinstance(q, CreateHostQuery)
    assert q.host == "web-server-01"


# ── ADD HOST ──────────────────────────────────────────────────────────────────

def test_add_host_single_group() -> None:
    q = parse("ADD HOST node1 TO GROUPS europe")
    assert isinstance(q, AddHostQuery)
    assert q.host == "node1"
    assert q.groups == ["europe"]


def test_add_host_multiple_groups() -> None:
    q = parse("ADD HOST node1 TO GROUPS europe, monitoring")
    assert isinstance(q, AddHostQuery)
    assert q.groups == ["europe", "monitoring"]


def test_add_host_missing_to_raises() -> None:
    with pytest.raises(ParseError):
        parse("ADD HOST node1 GROUPS europe")


# ── REMOVE HOST ───────────────────────────────────────────────────────────────

def test_remove_host_single_group() -> None:
    q = parse("REMOVE HOST node1 FROM GROUPS webservers")
    assert isinstance(q, RemoveHostQuery)
    assert q.host == "node1"
    assert q.groups == ["webservers"]


def test_remove_host_multiple_groups() -> None:
    q = parse("REMOVE HOST node1 FROM GROUPS webservers, europe")
    assert isinstance(q, RemoveHostQuery)
    assert q.groups == ["webservers", "europe"]


# ── DROP HOST ─────────────────────────────────────────────────────────────────

def test_drop_host() -> None:
    q = parse("DROP HOST node1")
    assert isinstance(q, DropHostQuery)
    assert q.host == "node1"
    assert q.keep_vars is False


def test_drop_host_keep_vars() -> None:
    q = parse("DROP HOST node1 KEEP VARS")
    assert isinstance(q, DropHostQuery)
    assert q.keep_vars is True


# ── parse errors ──────────────────────────────────────────────────────────────

def test_unknown_command_raises() -> None:
    with pytest.raises(ParseError, match="Unknown command"):
        parse("INVALID query")


def test_missing_where_raises() -> None:
    with pytest.raises(ParseError):
        parse('SELECT * FROM hostvars host = "node1"')


def test_missing_condition_field_raises() -> None:
    with pytest.raises(ParseError, match="host.*or.*group"):
        parse('SELECT * FROM hostvars WHERE "node1"')


def test_set_missing_value_raises() -> None:
    with pytest.raises(ParseError):
        parse('SET env WHERE host = "node1"')


def test_create_without_host_keyword_raises() -> None:
    with pytest.raises(ParseError):
        parse("CREATE node1")


def test_trailing_tokens_raises() -> None:
    with pytest.raises(ParseError):
        parse('SELECT * FROM hostvars WHERE host = "node1" EXTRA')


def test_empty_input_raises() -> None:
    with pytest.raises(ParseError):
        parse("")


# ── SHOW HOSTS ────────────────────────────────────────────────────────────────

def test_show_hosts_no_filter() -> None:
    q = parse("SHOW HOSTS")
    assert isinstance(q, ShowHostsQuery)
    assert q.pattern == "*"


def test_show_hosts_with_filter() -> None:
    q = parse('SHOW HOSTS WHERE host = "node*"')
    assert isinstance(q, ShowHostsQuery)
    assert q.pattern == "node*"


def test_show_hosts_case_insensitive() -> None:
    q = parse("show hosts")
    assert isinstance(q, ShowHostsQuery)


def test_show_hosts_wrong_condition_raises() -> None:
    with pytest.raises(ParseError):
        parse('SHOW HOSTS WHERE group = "webservers"')


# ── SHOW GROUPS ───────────────────────────────────────────────────────────────

def test_show_groups_no_filter() -> None:
    q = parse("SHOW GROUPS")
    assert isinstance(q, ShowGroupsQuery)
    assert q.pattern == "*"


def test_show_groups_with_filter() -> None:
    q = parse('SHOW GROUPS WHERE group = "web*"')
    assert isinstance(q, ShowGroupsQuery)
    assert q.pattern == "web*"


def test_show_groups_wrong_condition_raises() -> None:
    with pytest.raises(ParseError):
        parse('SHOW GROUPS WHERE host = "node1"')


def test_show_unknown_target_raises() -> None:
    with pytest.raises(ParseError, match="HOSTS or GROUPS"):
        parse("SHOW VARIABLES")
