"""Three-address code (TAC) representation and a deliberately naive generator.

A TAC program is an immutable tuple of Instr. Operands are either variable
names (str) or integer constants (int). Supported instruction forms:

    input x          op='input', dest='x'
    x = a            op='copy',  dest='x', a=a
    x = -a           op='neg',   dest='x', a=a
    x = a OP b       op in + - * / % <<
    print a          op='print', a=a
"""

import re
from dataclasses import dataclass, replace

from .parser import Assign, BinOp, Input, Neg, Num, Print, Var, parse

BINARY_OPS = ("+", "-", "*", "/", "%", "<<")
ARITH_OPS = frozenset(BINARY_OPS) | {"neg"}
COMMUTATIVE_OPS = frozenset({"+", "*"})
IO_OPS = frozenset({"input", "print"})
TEMP_RE = re.compile(r"t\d+$")


def is_var(operand):
    return isinstance(operand, str)


def is_const(operand):
    return isinstance(operand, int)


def is_temp(name):
    return isinstance(name, str) and TEMP_RE.match(name) is not None


@dataclass(frozen=True)
class Instr:
    op: str
    dest: object = None
    a: object = None
    b: object = None

    def uses(self):
        return [x for x in (self.a, self.b) if is_var(x)]

    def defines(self):
        return self.dest

    def replace_uses(self, var, new):
        a = new if self.a == var and is_var(self.a) else self.a
        b = new if self.b == var and is_var(self.b) else self.b
        return replace(self, a=a, b=b)

    def __str__(self):
        if self.op == "input":
            return f"input {self.dest}"
        if self.op == "print":
            return f"print {self.a}"
        if self.op == "copy":
            return f"{self.dest} = {self.a}"
        if self.op == "neg":
            return f"{self.dest} = -{self.a}"
        return f"{self.dest} = {self.a} {self.op} {self.b}"


def format_tac(program, numbered=True):
    if numbered:
        return "\n".join(f"{i:3d}: {ins}" for i, ins in enumerate(program))
    return "\n".join(str(ins) for ins in program)


class TACGenerator:
    """Naive syntax-directed translation: every operator result goes to a fresh
    temporary and every assignment is a copy from it. This leaves plenty of
    redundancy for the mutation-based optimiser to discover."""

    def __init__(self):
        self.code = []
        self.temp_count = 0

    def new_temp(self):
        self.temp_count += 1
        return f"t{self.temp_count}"

    def gen_program(self, ast):
        for stmt in ast.stmts:
            self.gen_stmt(stmt)
        return tuple(self.code)

    def gen_stmt(self, stmt):
        if isinstance(stmt, Input):
            for name in stmt.names:
                self.code.append(Instr("input", dest=name))
        elif isinstance(stmt, Assign):
            self.code.append(Instr("copy", dest=stmt.name, a=self.gen_expr(stmt.expr)))
        elif isinstance(stmt, Print):
            self.code.append(Instr("print", a=self.gen_expr(stmt.expr)))
        else:
            raise TypeError(f"unknown statement {stmt!r}")

    def gen_expr(self, node):
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Var):
            return node.name
        if isinstance(node, BinOp):
            left = self.gen_expr(node.left)
            right = self.gen_expr(node.right)
            temp = self.new_temp()
            self.code.append(Instr(node.op, dest=temp, a=left, b=right))
            return temp
        if isinstance(node, Neg):
            operand = self.gen_expr(node.operand)
            temp = self.new_temp()
            self.code.append(Instr("neg", dest=temp, a=operand))
            return temp
        raise TypeError(f"unknown expression {node!r}")


def compile_source(source):
    """Source text -> TAC program (tuple of Instr)."""
    return TACGenerator().gen_program(parse(source))


_TAC_BIN_RE = re.compile(r"^(\w+)\s*=\s*(-?\w+)\s*(<<|[+\-*/%])\s*(-?\w+)$")
_TAC_NEG_RE = re.compile(r"^(\w+)\s*=\s*-\s*([A-Za-z_]\w*)$")
_TAC_COPY_RE = re.compile(r"^(\w+)\s*=\s*(-?\w+)$")


def _operand(text):
    return int(text) if re.match(r"-?\d+$", text) else text


def parse_tac(text):
    """Parse TAC written in the format produced by format_tac(numbered=False).
    Used mainly by tests to state mutation inputs/outputs compactly."""
    program = []
    for raw in text.strip().splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        if line.startswith("input "):
            program.append(Instr("input", dest=line[6:].strip()))
        elif line.startswith("print "):
            program.append(Instr("print", a=_operand(line[6:].strip())))
        elif m := _TAC_BIN_RE.match(line):
            program.append(Instr(m.group(3), dest=m.group(1), a=_operand(m.group(2)), b=_operand(m.group(4))))
        elif m := _TAC_NEG_RE.match(line):
            program.append(Instr("neg", dest=m.group(1), a=_operand(m.group(2))))
        elif m := _TAC_COPY_RE.match(line):
            program.append(Instr("copy", dest=m.group(1), a=_operand(m.group(2))))
        else:
            raise ValueError(f"cannot parse TAC line: {raw!r}")
    return tuple(program)
