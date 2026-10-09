"""Mutation operators over TAC programs.

Each operator takes a program (tuple of Instr) and returns a list of Mutation
candidates, one per site where the rewrite applies. Operators only propose
rewrites; the search accepts a candidate only after differential validation.
"""

from dataclasses import dataclass

from execution.interpreter import ExecutionError, apply_op
from frontend.tac import COMMUTATIVE_OPS, IO_OPS, Instr, is_const, is_var


@dataclass(frozen=True)
class Mutation:
    op_id: str
    description: str
    program: tuple


def _replace_at(program, index, new_instr):
    return program[:index] + (new_instr,) + program[index + 1:]


def _delete_at(program, index):
    return program[:index] + program[index + 1:]


def _copy(dest, src):
    return Instr("copy", dest=dest, a=src)


def may_trap(ins):
    """Division/modulo by a non-constant (or zero) divisor can raise a runtime
    error, which is observable behaviour. Such instructions must not be deleted
    or moved across I/O."""
    if ins.op in ("/", "%"):
        return not (is_const(ins.b) and ins.b != 0)
    if ins.op == "<<":
        return not (is_const(ins.b) and ins.b >= 0)
    return False


# M1 ---------------------------------------------------------------------------
def zero_add_removal(program):
    """x = a + 0 | x = 0 + a | x = a - 0   ->   x = a"""
    out = []
    for i, ins in enumerate(program):
        src = None
        if ins.op == "+" and ins.b == 0 and is_var(ins.a):
            src = ins.a
        elif ins.op == "+" and ins.a == 0 and is_var(ins.b):
            src = ins.b
        elif ins.op == "-" and ins.b == 0 and is_var(ins.a):
            src = ins.a
        if src is not None:
            out.append(Mutation("M1", f"[{i}] {ins}  =>  {ins.dest} = {src}",
                                _replace_at(program, i, _copy(ins.dest, src))))
    return out


# M2 ---------------------------------------------------------------------------
def one_mul_removal(program):
    """x = a * 1 | x = 1 * a | x = a / 1   ->   x = a"""
    out = []
    for i, ins in enumerate(program):
        src = None
        if ins.op == "*" and ins.b == 1 and is_var(ins.a):
            src = ins.a
        elif ins.op == "*" and ins.a == 1 and is_var(ins.b):
            src = ins.b
        elif ins.op == "/" and ins.b == 1 and is_var(ins.a):
            src = ins.a
        if src is not None:
            out.append(Mutation("M2", f"[{i}] {ins}  =>  {ins.dest} = {src}",
                                _replace_at(program, i, _copy(ins.dest, src))))
    return out


# M3 ---------------------------------------------------------------------------
def constant_folding(program):
    """x = c1 OP c2   ->   x = c   (skipped when evaluation would fail)"""
    out = []
    for i, ins in enumerate(program):
        if ins.op in IO_OPS or ins.op == "copy":
            continue
        operands = [ins.a] if ins.op == "neg" else [ins.a, ins.b]
        if not all(is_const(x) for x in operands):
            continue
        try:
            value = apply_op(ins.op, *operands)
        except ExecutionError:
            continue
        out.append(Mutation("M3", f"[{i}] {ins}  =>  {ins.dest} = {value}",
                            _replace_at(program, i, _copy(ins.dest, value))))
    return out


# M4 ---------------------------------------------------------------------------
def strength_reduction(program):
    """x = a * 2 -> x = a + a ;  x = a * 2^k (k >= 2) -> x = a << k"""
    out = []
    for i, ins in enumerate(program):
        if ins.op != "*":
            continue
        if is_var(ins.a) and is_const(ins.b):
            var, const = ins.a, ins.b
        elif is_const(ins.a) and is_var(ins.b):
            var, const = ins.b, ins.a
        else:
            continue
        if const == 2:
            new = Instr("+", dest=ins.dest, a=var, b=var)
        elif const > 2 and const & (const - 1) == 0:
            new = Instr("<<", dest=ins.dest, a=var, b=const.bit_length() - 1)
        else:
            continue
        out.append(Mutation("M4", f"[{i}] {ins}  =>  {new}", _replace_at(program, i, new)))
    return out


# M5 ---------------------------------------------------------------------------
def dead_assignment_removal(program):
    """Remove an assignment whose value is never read (backward liveness).
    'input' is never removed: it consumes a value from the input stream.
    A possibly-trapping division is never removed: its error is observable."""
    out = []
    live = set()
    dead = []
    for i in range(len(program) - 1, -1, -1):
        ins = program[i]
        if ins.dest is not None and ins.op != "input" and ins.dest not in live and not may_trap(ins):
            dead.append(i)
        if ins.dest is not None:
            live.discard(ins.dest)
        live.update(ins.uses())
    for i in reversed(dead):
        out.append(Mutation("M5", f"[{i}] {program[i]}  =>  (removed)", _delete_at(program, i)))
    return out


# M6 ---------------------------------------------------------------------------
def _expr_key(ins):
    if ins.op in COMMUTATIVE_OPS:
        a, b = sorted((ins.a, ins.b), key=repr)
        return (ins.op, a, b)
    return (ins.op, ins.a, ins.b)


def common_subexpression_elimination(program):
    """t_j = a OP b, where t_i = a OP b is still available  ->  t_j = t_i"""
    out = []
    for i, first in enumerate(program):
        if first.op in IO_OPS or first.op == "copy":
            continue
        if first.dest in first.uses():  # x = x + 1: expression changes immediately
            continue
        key = _expr_key(first)
        killers = {first.dest, *first.uses()}
        for j in range(i + 1, len(program)):
            later = program[j]
            if later.op == first.op and _expr_key(later) == key and later.dest != first.dest:
                out.append(Mutation("M6", f"[{j}] {later}  =>  {later.dest} = {first.dest}  (reuse [{i}])",
                                    _replace_at(program, j, _copy(later.dest, first.dest))))
            if later.dest in killers:
                break
    return out


# M7 / M9 ----------------------------------------------------------------------
def _is_dead_after(program, i):
    """True if the value defined at program[i] is never read afterwards."""
    dest = program[i].dest
    for ins in program[i + 1:]:
        if dest in ins.uses():
            return False
        if ins.dest == dest:
            return True
    return True  # only printed values are observable at program exit


def _propagate(program, i, op_id):
    """Forward-substitute the source of copy program[i] into later uses of its
    destination, until either side is redefined. If that leaves the copy dead it
    is deleted in the same step: on its own, propagation does not change the
    cost, so a greedy search would otherwise never take it."""
    src_ins = program[i]
    dest, src = src_ins.dest, src_ins.a
    new_program = list(program)
    replaced = 0
    for j in range(i + 1, len(program)):
        ins = program[j]
        if dest in ins.uses():
            new_program[j] = ins.replace_uses(dest, src)
            replaced += 1
        if ins.dest == dest or (is_var(src) and ins.dest == src):
            break
    if not replaced:
        return None
    description = f"[{i}] {src_ins}: substituted {src} for {dest} in {replaced} use(s)"
    new_program = tuple(new_program)
    if _is_dead_after(new_program, i):
        new_program = _delete_at(new_program, i)
        description += ", copy removed"
    return Mutation(op_id, description, new_program)


def constant_propagation(program):
    """x = c ; ... use x ...   ->   ... use c ..."""
    out = []
    for i, ins in enumerate(program):
        if ins.op == "copy" and is_const(ins.a):
            mutation = _propagate(program, i, "M7")
            if mutation:
                out.append(mutation)
    return out


def copy_propagation(program):
    """x = y ; ... use x ...   ->   ... use y ...   (helper operator, see README)"""
    out = []
    for i, ins in enumerate(program):
        if ins.op == "copy" and is_var(ins.a) and ins.a != ins.dest:
            mutation = _propagate(program, i, "M9")
            if mutation:
                out.append(mutation)
    return out


# M8 ---------------------------------------------------------------------------
def _independent(x, y):
    if (x.op in IO_OPS or may_trap(x)) and (y.op in IO_OPS or may_trap(y)):
        return False  # keep the order of observable events: I/O and runtime errors
    x_def, y_def = x.defines(), y.defines()
    if x_def is not None and (x_def in y.uses() or x_def == y_def):
        return False  # RAW / WAW
    if y_def is not None and y_def in x.uses():
        return False  # WAR
    return True


def instruction_reordering(program):
    """Swap adjacent independent instructions (useful for hiding RAW stalls)."""
    out = []
    for i in range(len(program) - 1):
        x, y = program[i], program[i + 1]
        if _independent(x, y):
            swapped = program[:i] + (y, x) + program[i + 2:]
            out.append(Mutation("M8", f"swap [{i}] {x}  <->  [{i + 1}] {y}", swapped))
    return out


# MX (unsafe, demo only) --------------------------------------------------------
def unchecked_reordering(program):
    """Swaps ANY adjacent pair with no dependence check. Used with --unsafe to
    demonstrate that the validator rejects semantically wrong candidates."""
    out = []
    for i in range(len(program) - 1):
        x, y = program[i], program[i + 1]
        if not _independent(x, y):
            swapped = program[:i] + (y, x) + program[i + 2:]
            out.append(Mutation("MX", f"unchecked swap [{i}] <-> [{i + 1}]", swapped))
    return out


OPERATOR_NAMES = {
    "M1": "Zero-Add Removal",
    "M2": "One-Mul Removal",
    "M3": "Constant Folding",
    "M4": "Strength Reduction",
    "M5": "Dead Assignment Removal",
    "M6": "Common Subexpression Elimination",
    "M7": "Constant Propagation",
    "M8": "Instruction Reordering",
    "M9": "Copy Propagation",
    "MX": "Unchecked Reordering (unsafe)",
}

OPERATORS = [
    ("M1", zero_add_removal),
    ("M2", one_mul_removal),
    ("M3", constant_folding),
    ("M4", strength_reduction),
    ("M5", dead_assignment_removal),
    ("M6", common_subexpression_elimination),
    ("M7", constant_propagation),
    ("M8", instruction_reordering),
    ("M9", copy_propagation),
]
UNSAFE_OPERATORS = [("MX", unchecked_reordering)]


def generate_mutations(program, operators=OPERATORS):
    candidates = []
    for _, fn in operators:
        candidates.extend(fn(program))
    return candidates
