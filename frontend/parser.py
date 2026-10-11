"""Recursive-descent parser producing an AST for MiniArith.

Grammar:
    program := stmt* EOF
    stmt    := 'input' ID (',' ID)* ';'
             | ID '=' expr ';'
             | 'print' expr ';'
             | 'if' '(' cond ')' block ('else' block)?      (staged extension)
    block   := '{' stmt* '}'
    cond    := expr (RELOP expr)?          RELOP in < <= > >= == !=
    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/' | '%') unary)*
    unary   := '-' unary | atom
    atom    := NUM | ID | '(' expr ')'

Relational operators are only allowed as the top-level operator of an `if`
condition; they yield 1 or 0. A variable is "defined" after an if/else only if
both branches define it (definite assignment), so a program accepted by the
parser can never read an undefined variable.
"""

from dataclasses import dataclass

from .lexer import tokenize


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class Num:
    value: int


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class BinOp:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class Neg:
    operand: object


@dataclass(frozen=True)
class Input:
    names: tuple


@dataclass(frozen=True)
class Assign:
    name: str
    expr: object


@dataclass(frozen=True)
class Print:
    expr: object


@dataclass(frozen=True)
class If:
    cond: object
    then: tuple
    orelse: tuple


RELOPS = ("<", "<=", ">", ">=", "==", "!=")


@dataclass(frozen=True)
class Program:
    stmts: tuple


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.defined = set()

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind, value=None):
        tok = self.peek()
        if tok.kind != kind or (value is not None and tok.value != value):
            want = value or kind
            raise ParseError(f"line {tok.line}: expected {want!r}, found {tok.value or tok.kind!r}")
        return self.advance()

    def at_op(self, *values):
        tok = self.peek()
        return tok.kind == "OP" and tok.value in values

    def parse_program(self):
        stmts = []
        while self.peek().kind != "EOF":
            stmts.append(self.parse_stmt())
        return Program(tuple(stmts))

    def parse_stmt(self):
        tok = self.peek()
        if tok.kind == "INPUT":
            self.advance()
            names = [self.expect("ID").value]
            while self.at_op(","):
                self.advance()
                names.append(self.expect("ID").value)
            self.expect("OP", ";")
            self.defined.update(names)
            return Input(tuple(names))
        if tok.kind == "PRINT":
            self.advance()
            expr = self.parse_expr()
            self.expect("OP", ";")
            return Print(expr)
        if tok.kind == "ID":
            name = self.advance().value
            self.expect("OP", "=")
            expr = self.parse_expr()
            self.expect("OP", ";")
            self.defined.add(name)
            return Assign(name, expr)
        if tok.kind == "IF":
            return self.parse_if()
        raise ParseError(f"line {tok.line}: unexpected {tok.value or tok.kind!r} at start of statement")

    def parse_block(self):
        self.expect("OP", "{")
        stmts = []
        while not self.at_op("}"):
            if self.peek().kind == "EOF":
                raise ParseError(f"line {self.peek().line}: expected '}}' before end of input")
            stmts.append(self.parse_stmt())
        self.advance()
        return tuple(stmts)

    def parse_if(self):
        self.advance()
        self.expect("OP", "(")
        cond = self.parse_expr()
        if self.at_op(*RELOPS):
            op = self.advance().value
            cond = BinOp(op, cond, self.parse_expr())
        self.expect("OP", ")")
        before = set(self.defined)
        then = self.parse_block()
        then_defined = self.defined
        self.defined = set(before)
        orelse = ()
        if self.peek().kind == "ELSE":
            self.advance()
            orelse = self.parse_block()
        # definite assignment: defined afterwards only if defined on both paths
        self.defined = before | (then_defined & self.defined)
        return If(cond, then, orelse)

    def parse_expr(self):
        node = self.parse_term()
        while self.at_op("+", "-"):
            op = self.advance().value
            node = BinOp(op, node, self.parse_term())
        return node

    def parse_term(self):
        node = self.parse_unary()
        while self.at_op("*", "/", "%"):
            op = self.advance().value
            node = BinOp(op, node, self.parse_unary())
        return node

    def parse_unary(self):
        if self.at_op("-"):
            self.advance()
            return Neg(self.parse_unary())
        return self.parse_atom()

    def parse_atom(self):
        tok = self.peek()
        if tok.kind == "NUM":
            self.advance()
            return Num(int(tok.value))
        if tok.kind == "ID":
            self.advance()
            if tok.value not in self.defined:
                raise ParseError(f"line {tok.line}: variable {tok.value!r} used before definition")
            return Var(tok.value)
        if self.at_op("("):
            self.advance()
            node = self.parse_expr()
            self.expect("OP", ")")
            return node
        raise ParseError(f"line {tok.line}: unexpected {tok.value or tok.kind!r} in expression")


def parse(source):
    return Parser(tokenize(source)).parse_program()
