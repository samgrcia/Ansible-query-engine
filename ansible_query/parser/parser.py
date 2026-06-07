from ansible_query.parser.ast import (
    Condition,
    CreateHostQuery,
    DropHostQuery,
    Query,
    RemoveHostQuery,
    SelectQuery,
    SetQuery,
    UnsetQuery,
)
from ansible_query.parser.lexer import Token, TokenType, tokenize


class ParseError(Exception):
    pass


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    # ── primitives ────────────────────────────────────────────────────────────

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        if token.type != TokenType.EOF:
            self._pos += 1
        return token

    def _check(self, *types: TokenType) -> bool:
        return self._peek().type in types

    def _expect(self, *types: TokenType) -> Token:
        token = self._peek()
        if token.type not in types:
            expected = " or ".join(t.name for t in types)
            raise ParseError(
                f"Expected {expected}, got {token.value!r} at position {token.pos}"
            )
        return self._advance()

    def _expect_identifier(self) -> str:
        return str(self._expect(TokenType.IDENTIFIER).value)

    def _expect_source(self) -> str:
        return str(self._expect(TokenType.HOSTVARS, TokenType.GROUPVARS).value).lower()

    # ── top-level dispatch ────────────────────────────────────────────────────

    def parse(self) -> Query:
        t = self._peek()
        if t.type == TokenType.SELECT:
            return self._parse_select()
        if t.type == TokenType.SET:
            return self._parse_set()
        if t.type == TokenType.UNSET:
            return self._parse_unset()
        if t.type == TokenType.CREATE:
            return self._parse_create()
        if t.type == TokenType.REMOVE:
            return self._parse_remove()
        if t.type == TokenType.DROP:
            return self._parse_drop()
        raise ParseError(f"Unknown command {t.value!r} at position {t.pos}")

    # ── SELECT ────────────────────────────────────────────────────────────────

    def _parse_select(self) -> SelectQuery:
        self._expect(TokenType.SELECT)

        if self._check(TokenType.STAR):
            self._advance()
            columns: list[str] = ["*"]
        else:
            columns = [self._expect_identifier()]
            while self._check(TokenType.COMMA):
                self._advance()
                columns.append(self._expect_identifier())

        self._expect(TokenType.FROM)

        sources: list[str] = [self._expect_source()]
        while self._check(TokenType.COMMA):
            self._advance()
            sources.append(self._expect_source())

        self._expect(TokenType.WHERE)
        condition = self._parse_condition()
        self._expect(TokenType.EOF)
        return SelectQuery(columns=columns, sources=sources, condition=condition)

    # ── SET ───────────────────────────────────────────────────────────────────

    def _parse_set(self) -> SetQuery:
        self._expect(TokenType.SET)
        variable = self._expect_identifier()
        self._expect(TokenType.EQ)
        val_token = self._expect(TokenType.STRING, TokenType.INTEGER)
        self._expect(TokenType.WHERE)
        condition = self._parse_condition()

        encrypt = False
        if self._check(TokenType.ENCRYPT):
            self._advance()
            encrypt = True

        self._expect(TokenType.EOF)
        return SetQuery(
            variable=variable,
            value=val_token.value,
            condition=condition,
            encrypt=encrypt,
        )

    # ── UNSET ─────────────────────────────────────────────────────────────────

    def _parse_unset(self) -> UnsetQuery:
        self._expect(TokenType.UNSET)
        variable = self._expect_identifier()
        self._expect(TokenType.FROM)
        source = self._expect_source()
        self._expect(TokenType.WHERE)
        condition = self._parse_condition()
        self._expect(TokenType.EOF)
        return UnsetQuery(variable=variable, source=source, condition=condition)

    # ── CREATE HOST ───────────────────────────────────────────────────────────

    def _parse_create(self) -> CreateHostQuery:
        self._expect(TokenType.CREATE)
        self._expect(TokenType.HOST)
        host = self._expect_identifier()

        groups: list[str] = []
        if self._check(TokenType.IN):
            self._advance()
            self._expect(TokenType.GROUPS)
            groups.append(self._expect_identifier())
            while self._check(TokenType.COMMA):
                self._advance()
                groups.append(self._expect_identifier())

        self._expect(TokenType.EOF)
        return CreateHostQuery(host=host, groups=groups)

    # ── REMOVE HOST ───────────────────────────────────────────────────────────

    def _parse_remove(self) -> RemoveHostQuery:
        self._expect(TokenType.REMOVE)
        self._expect(TokenType.HOST)
        host = self._expect_identifier()
        self._expect(TokenType.FROM)
        self._expect(TokenType.GROUPS)
        groups = [self._expect_identifier()]
        while self._check(TokenType.COMMA):
            self._advance()
            groups.append(self._expect_identifier())
        self._expect(TokenType.EOF)
        return RemoveHostQuery(host=host, groups=groups)

    # ── DROP HOST ─────────────────────────────────────────────────────────────

    def _parse_drop(self) -> DropHostQuery:
        self._expect(TokenType.DROP)
        self._expect(TokenType.HOST)
        host = self._expect_identifier()

        keep_vars = False
        if self._check(TokenType.KEEP):
            self._advance()
            self._expect(TokenType.VARS)
            keep_vars = True

        self._expect(TokenType.EOF)
        return DropHostQuery(host=host, keep_vars=keep_vars)

    # ── condition ─────────────────────────────────────────────────────────────

    def _parse_condition(self) -> Condition:
        if self._check(TokenType.HOST):
            self._advance()
            kind = "host"
        elif self._check(TokenType.GROUP):
            self._advance()
            kind = "group"
        else:
            t = self._peek()
            raise ParseError(
                f"Expected 'host' or 'group' in WHERE clause, "
                f"got {t.value!r} at position {t.pos}"
            )
        self._expect(TokenType.EQ)
        pattern = str(self._expect(TokenType.STRING).value)
        return Condition(kind=kind, pattern=pattern)


# ── public API ────────────────────────────────────────────────────────────────

def parse(query: str) -> Query:
    """Parse a query string and return the corresponding AST node."""
    tokens = tokenize(query)
    return _Parser(tokens).parse()
