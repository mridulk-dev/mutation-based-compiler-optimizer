"""Three-address code (TAC) representation and a deliberately naive generator.

A TAC program is an immutable tuple of Instr. Operands are either variable
names (str) or integer constants (int). Supported instruction forms:

    input x          op='input', dest='x'
    x = a            op='copy',  dest='x', a=a
    x = -a           op='neg',   dest='x', a=a
    x = a OP b       op in + - * / % <<  and the relational ops < <= > >= == !=
    print a          op='print', a=a

Control flow (staged extension, only produced for programs that use `if`):

    label L1         op='label', target='L1'   (pseudo-instruction, not executed)
    goto L1          op='goto',  target='L1'
    ifz a goto L1    op='ifz',   a=a, target='L1'   (jump when a == 0)

Straight-line programs never contain these, so their TAC is exactly the
Review-1 TAC.
"""

import re
from dataclasses import dataclass, replace

from .parser import Assign, BinOp, If, Input, Neg, Num, Print, Var, parse

REL_OPS = ("<", "<=", ">", ">=", "==", "!=")
BINARY_OPS = ("+", "-", "*", "/", "%", "<<") + REL_OPS
ARITH_OPS = frozenset(BINARY_OPS) | {"neg"}
COMMUTATIVE_OPS = frozenset({"+", "*", "==", "!="})
IO_OPS = frozenset({"input", "print"})
BRANCH_OPS = frozenset({"goto", "ifz"})
CONTROL_OPS = BRANCH_OPS | {"label"}
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
    target: object = None  # jump target / label name (control flow only)

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
        if self.op == "neg":  # -(5) keeps "negate a constant" distinct from the constant -5
            return f"{self.dest} = -({self.a})" if is_const(self.a) else f"{self.dest} = -{self.a}"
        if self.op == "label":
            return f"label {self.target}"
        if self.op == "goto":
            return f"goto {self.target}"
        if self.op == "ifz":
            return f"ifz {self.a} goto {self.target}"
        return f"{self.dest} = {self.a} {self.op} {self.b}"


def format_tac(program, numbered=True):
    if numbered:
        return "\n".join(f"{i:3d}: {ins}" for i, ins in enumerate(program))
    return "\n".join(str(ins) for ins in program)


def has_control_flow(program):
    return any(ins.op in CONTROL_OPS for ins in program)


class TACGenerator:
    """Naive syntax-directed translation: every operator result goes to a fresh
    temporary and every assignment is a copy from it. This leaves plenty of
    redundancy for the mutation-based optimiser to discover."""

    def __init__(self):
        self.code = []
        self.temp_count = 0
        self.label_count = 0

    def new_temp(self):
        self.temp_count += 1
        return f"t{self.temp_count}"

    def new_label(self):
        self.label_count += 1
        return f"L{self.label_count}"

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
        elif isinstance(stmt, If):
            cond = self.gen_expr(stmt.cond)
            else_label = self.new_label()
            self.code.append(Instr("ifz", a=cond, target=else_label))
            for s in stmt.then:
                self.gen_stmt(s)
            if stmt.orelse:
                end_label = self.new_label()
                self.code.append(Instr("goto", target=end_label))
                self.code.append(Instr("label", target=else_label))
                for s in stmt.orelse:
                    self.gen_stmt(s)
                self.code.append(Instr("label", target=end_label))
            else:
                self.code.append(Instr("label", target=else_label))
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


# --- basic blocks and control-flow graph ------------------------------------------
@dataclass(frozen=True)
class BasicBlock:
    index: int
    start: int          # first instruction index (a label, if the block has one)
    end: int            # one past the last instruction index
    body_start: int     # first non-label instruction
    body_end: int       # one past the last non-terminator instruction
    label: object       # label name or None
    successors: tuple   # indices of successor blocks


def basic_blocks(program):
    """Split a TAC program into basic blocks and link them into a CFG.

    Leaders: instruction 0, every label, every instruction after a branch.
    A block is [optional label] body [optional goto/ifz terminator]. Mutation
    operators only ever see a block *body*, so they cannot touch labels or jumps.
    """
    n = len(program)
    leaders = {0} if n else set()
    for i, ins in enumerate(program):
        if ins.op == "label":
            leaders.add(i)
        if ins.op in BRANCH_OPS and i + 1 < n:
            leaders.add(i + 1)
    starts = sorted(leaders)
    label_block = {}
    raw = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else n
        label = program[s].target if program[s].op == "label" else None
        body_start = s + 1 if label is not None else s
        body_end = e - 1 if e - 1 >= body_start and program[e - 1].op in BRANCH_OPS else e
        if label is not None:
            label_block[label] = k
        raw.append((s, e, body_start, body_end, label))
    blocks = []
    for k, (s, e, bs, be, label) in enumerate(raw):
        succ = []
        last = program[e - 1] if e > s else None
        if last is not None and last.op in BRANCH_OPS:
            if last.target not in label_block:
                raise ValueError(f"jump to undefined label {last.target!r}")
            succ.append(label_block[last.target])
        if (last is None or last.op != "goto") and k + 1 < len(raw):
            succ.append(k + 1)
        blocks.append(BasicBlock(k, s, e, bs, be, label, tuple(dict.fromkeys(succ))))
    return blocks


def cfg_edges(program):
    return [(b.index, s) for b in basic_blocks(program) for s in b.successors]


# --- TAC text parser ---------------------------------------------------------------
_TAC_BIN_RE = re.compile(r"^(\w+)\s*=\s*(-?\w+)\s*(<<|<=|>=|==|!=|[<>+\-*/%])\s*(-?\w+)$")
_TAC_NEG_RE = re.compile(r"^(\w+)\s*=\s*-\s*([A-Za-z_]\w*)$")
_TAC_NEG_CONST_RE = re.compile(r"^(\w+)\s*=\s*-\(\s*(-?\d+)\s*\)$")
_TAC_COPY_RE = re.compile(r"^(\w+)\s*=\s*(-?\w+)$")
_TAC_IFZ_RE = re.compile(r"^ifz\s+(-?\w+)\s+goto\s+(\w+)$")


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
        elif line.startswith("label "):
            program.append(Instr("label", target=line[6:].strip()))
        elif line.startswith("goto "):
            program.append(Instr("goto", target=line[5:].strip()))
        elif m := _TAC_IFZ_RE.match(line):
            program.append(Instr("ifz", a=_operand(m.group(1)), target=m.group(2)))
        elif m := _TAC_BIN_RE.match(line):
            program.append(Instr(m.group(3), dest=m.group(1), a=_operand(m.group(2)), b=_operand(m.group(4))))
        elif m := _TAC_NEG_CONST_RE.match(line):
            program.append(Instr("neg", dest=m.group(1), a=int(m.group(2))))
        elif m := _TAC_NEG_RE.match(line):
            program.append(Instr("neg", dest=m.group(1), a=_operand(m.group(2))))
        elif m := _TAC_COPY_RE.match(line):
            program.append(Instr("copy", dest=m.group(1), a=_operand(m.group(2))))
        else:
            raise ValueError(f"cannot parse TAC line: {raw!r}")
    return tuple(program)


# --- canonical form (used for dataset deduplication) ---------------------------------
def canonical_tac(program):
    """Alpha-rename a TAC program: user variables become v0, v1, ... and
    temporaries/labels are renumbered in order of first appearance. Two programs
    with the same canonical TAC differ only in identifier names."""
    names, temps, labels = {}, {}, {}

    def ren(x):
        if not is_var(x):
            return x
        if is_temp(x):
            return temps.setdefault(x, f"t{len(temps) + 1}")
        return names.setdefault(x, f"v{len(names)}")

    out = []
    for ins in program:
        target = labels.setdefault(ins.target, f"L{len(labels) + 1}") if ins.target is not None else None
        out.append(Instr(ins.op, dest=ren(ins.dest) if ins.dest is not None else None,
                         a=ren(ins.a), b=ren(ins.b), target=target))
    return tuple(out)
