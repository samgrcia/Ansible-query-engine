from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    # SQL-like keywords (uppercase in the language)
    SELECT = auto()
    FROM = auto()
    WHERE = auto()
    SET = auto()
    UNSET = auto()
    CREATE = auto()
    REMOVE = auto()
    DROP = auto()
    # Structural keywords
    HOST = auto()
    GROUP = auto()    # used in WHERE group = "..."
    IN = auto()
    GROUPS = auto()
    KEEP = auto()
    VARS = auto()
    ENCRYPT = auto()
    # Source keywords
    HOSTVARS = auto()
    GROUPVARS = auto()
    # Punctuation
    EQ = auto()       # =
    COMMA = auto()    # ,
    STAR = auto()     # *
    # Literals
    STRING = auto()   # "..."
    INTEGER = auto()  # [0-9]+
    # Bare word (not a keyword)
    IDENTIFIER = auto()
    EOF = auto()


_KEYWORDS: dict[str, TokenType] = {
    "select":    TokenType.SELECT,
    "from":      TokenType.FROM,
    "where":     TokenType.WHERE,
    "set":       TokenType.SET,
    "unset":     TokenType.UNSET,
    "create":    TokenType.CREATE,
    "remove":    TokenType.REMOVE,
    "drop":      TokenType.DROP,
    "host":      TokenType.HOST,
    "group":     TokenType.GROUP,
    "in":        TokenType.IN,
    "groups":    TokenType.GROUPS,
    "keep":      TokenType.KEEP,
    "vars":      TokenType.VARS,
    "encrypt":   TokenType.ENCRYPT,
    "hostvars":  TokenType.HOSTVARS,
    "groupvars": TokenType.GROUPVARS,
}


@dataclass
class Token:
    type: TokenType
    value: str | int  # str for STRING/IDENTIFIER/keywords, int for INTEGER
    pos: int


class LexError(Exception):
    pass


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    n = len(text)

    while pos < n:
        c = text[pos]

        if c.isspace():
            pos += 1
            continue

        # String literal
        if c == '"':
            end = pos + 1
            while end < n and text[end] != '"':
                end += 1
            if end >= n:
                raise LexError(f"Unterminated string at position {pos}")
            tokens.append(Token(TokenType.STRING, text[pos + 1 : end], pos))
            pos = end + 1
            continue

        # Single-character punctuation
        if c == '=':
            tokens.append(Token(TokenType.EQ, '=', pos))
            pos += 1
            continue
        if c == ',':
            tokens.append(Token(TokenType.COMMA, ',', pos))
            pos += 1
            continue
        if c == '*':
            tokens.append(Token(TokenType.STAR, '*', pos))
            pos += 1
            continue

        # Integer literal
        if c.isdigit():
            end = pos
            while end < n and text[end].isdigit():
                end += 1
            tokens.append(Token(TokenType.INTEGER, int(text[pos:end]), pos))
            pos = end
            continue

        # Identifier or keyword (letters, digits, underscore, hyphen)
        if c.isalpha() or c == '_':
            end = pos
            while end < n and (text[end].isalnum() or text[end] in ('_', '-')):
                end += 1
            word = text[pos:end]
            tt = _KEYWORDS.get(word.lower(), TokenType.IDENTIFIER)
            tokens.append(Token(tt, word, pos))
            pos = end
            continue

        raise LexError(f"Unexpected character {c!r} at position {pos}")

    tokens.append(Token(TokenType.EOF, '', n))
    return tokens
