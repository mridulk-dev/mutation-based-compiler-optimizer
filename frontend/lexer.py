"""Lexer for the MiniArith language.

Language summary (integer only):

    input a, b;          # read values from the input stream
    x = (a + 2) * b;     # assignment with + - * / % and unary minus
    print x - 1;         # emit a value to the output stream
    if (x < b) { print x; } else { print b; }   # staged extension: if/else

Straight-line programs (no `if`) are the core, fully evaluated subset.

Identifiers of the form t<digits> are reserved for compiler temporaries.
"""

import re
from dataclasses import dataclass

KEYWORDS = {"input", "print", "if", "else"}

TOKEN_SPEC = [
    ("NUM", r"\d+"),
    ("ID", r"[A-Za-z_][A-Za-z_0-9]*"),
    ("OP", r"==|!=|<=|>=|[<>+\-*/%=(),;{}]"),
    ("COMMENT", r"#[^\n]*"),
    ("NEWLINE", r"\n"),
    ("SKIP", r"[ \t\r]+"),
    ("MISMATCH", r"."),
]
TOKEN_RE = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in TOKEN_SPEC))
TEMP_RE = re.compile(r"t\d+$")


class LexError(Exception):
    pass


@dataclass(frozen=True)
class Token:
    kind: str  # NUM, ID, OP, INPUT, PRINT, IF, ELSE, EOF
    value: str
    line: int


def tokenize(source):
    tokens = []
    line = 1
    for match in TOKEN_RE.finditer(source):
        kind, value = match.lastgroup, match.group()
        if kind == "NEWLINE":
            line += 1
        elif kind in ("SKIP", "COMMENT"):
            continue
        elif kind == "MISMATCH":
            raise LexError(f"line {line}: unexpected character {value!r}")
        elif kind == "ID" and value in KEYWORDS:
            tokens.append(Token(value.upper(), value, line))
        elif kind == "ID" and TEMP_RE.match(value):
            raise LexError(f"line {line}: identifier {value!r} is reserved for temporaries")
        else:
            tokens.append(Token(kind, value, line))
    tokens.append(Token("EOF", "", line))
    return tokens
