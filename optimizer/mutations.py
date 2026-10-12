"""Mutation operators over TAC programs.

Each operator takes a program (tuple of Instr) and returns a list of Mutation
candidates, one per site where the rewrite applies. Operators only propose
rewrites; the search accepts a candidate only after differential validation.

Control flow (staged extension): for programs containing labels/jumps,
`generate_mutations` applies every local operator to one basic block at a time.
The operator sees the block body plus its terminating jump (never a label) and a
conservative `live_out` set = every variable read anywhere outside that slice.
Because if/else code is acyclic, that set over-approximates true liveness, so
dead-code decisions stay sound. Straight-line programs are a single block with
an empty live_out set, i.e. exactly the Review-1 behaviour.
"""

from dataclasses import dataclass

from execution.interpreter import ExecutionError, apply_op
from frontend.tac import (BRANCH_OPS, COMMUTATIVE_OPS, CONTROL_OPS, IO_OPS, Instr, basic_blocks,
                          has_control_flow, is_const, is_var)

NO_LIVE = frozenset()


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
def zero_add_removal(program, live_out=NO_LIVE):
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
def one_mul_removal(program, live_out=NO_LIVE):
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
def constant_folding(program, live_out=NO_LIVE):
    """x = c1 OP c2   ->   x = c   (skipped when evaluation would fail)"""
    out = []
    for i, ins in enumerate(program):
        if ins.op in IO_OPS or ins.op in CONTROL_OPS or ins.op == "copy":
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
def strength_reduction(program, live_out=NO_LIVE):
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
def dead_assignment_removal(program, live_out=NO_LIVE):
    """Remove an assignment whose value is never read (backward liveness).
    'input' is never removed: it consumes a value from the input stream.
    A possibly-trapping division is never removed: its error is observable."""
    out = []
    live = set(live_out)
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


def common_subexpression_elimination(program, live_out=NO_LIVE):
    """t_j = a OP b, where t_i = a OP b is still available  ->  t_j = t_i"""
    out = []
    for i, first in enumerate(program):
        if first.op in IO_OPS or first.op in CONTROL_OPS or first.op == "copy":
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
def _is_dead_after(program, i, live_out=NO_LIVE):
    """True if the value defined at program[i] is never read afterwards."""
    dest = program[i].dest
    for ins in program[i + 1:]:
        if dest in ins.uses():
            return False
        if ins.dest == dest:
            return True
    return dest not in live_out  # straight-line: only printed values are observable at exit


def _propagate(program, i, op_id, live_out=NO_LIVE):
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
    if _is_dead_after(new_program, i, live_out):
        new_program = _delete_at(new_program, i)
        description += ", copy removed"
    return Mutation(op_id, description, new_program)


def constant_propagation(program, live_out=NO_LIVE):
    """x = c ; ... use x ...   ->   ... use c ..."""
    out = []
    for i, ins in enumerate(program):
        if ins.op == "copy" and is_const(ins.a):
            mutation = _propagate(program, i, "M7", live_out)
            if mutation:
                out.append(mutation)
    return out


def copy_propagation(program, live_out=NO_LIVE):
    """x = y ; ... use x ...   ->   ... use y ...   (helper operator, see README)"""
    out = []
    for i, ins in enumerate(program):
        if ins.op == "copy" and is_var(ins.a) and ins.a != ins.dest:
            mutation = _propagate(program, i, "M9", live_out)
            if mutation:
                out.append(mutation)
    return out


# M8 ---------------------------------------------------------------------------
def _independent(x, y):
    if x.op in CONTROL_OPS or y.op in CONTROL_OPS:
        return False  # never move code across a label or a jump
    if (x.op in IO_OPS or may_trap(x)) and (y.op in IO_OPS or may_trap(y)):
        return False  # keep the order of observable events: I/O and runtime errors
    x_def, y_def = x.defines(), y.defines()
    if x_def is not None and (x_def in y.uses() or x_def == y_def):
        return False  # RAW / WAW
    if y_def is not None and y_def in x.uses():
        return False  # WAR
    return True


def instruction_reordering(program, live_out=NO_LIVE):
    """Swap adjacent independent instructions (useful for hiding RAW stalls)."""
    out = []
    for i in range(len(program) - 1):
        x, y = program[i], program[i + 1]
        if _independent(x, y):
            swapped = program[:i] + (y, x) + program[i + 2:]
            out.append(Mutation("M8", f"swap [{i}] {x}  <->  [{i + 1}] {y}", swapped))
    return out


# M10 (control-flow extension) ---------------------------------------------------
def _drop_unreferenced_labels(program):
    refs = {ins.target for ins in program if ins.op in BRANCH_OPS}
    return tuple(ins for ins in program if not (ins.op == "label" and ins.target not in refs))


def _reachable_code(program):
    if not program:
        return program
    blocks = basic_blocks(program)
    reach, stack = set(), [0]
    while stack:
        b = stack.pop()
        if b not in reach:
            reach.add(b)
            stack.extend(blocks[b].successors)
    return tuple(ins for blk in blocks if blk.index in reach for ins in program[blk.start:blk.end])


def cleanup_cfg(program):
    """Semantics-preserving CFG clean-up, repeated to a fixpoint: drop labels no
    jump refers to, drop a goto that jumps to the immediately following label,
    and delete basic blocks unreachable from the entry block."""
    while True:
        new = _drop_unreferenced_labels(program)
        new = tuple(ins for k, ins in enumerate(new)
                    if not (ins.op == "goto" and k + 1 < len(new) and new[k + 1].op == "label"
                            and new[k + 1].target == ins.target))
        new = _reachable_code(new)
        if new == program:
            return new
        program = new


def branch_folding(program, live_out=NO_LIVE):
    """ifz c goto L with a constant c: c == 0 -> goto L, c != 0 -> fall through.
    The dead branch, redundant jumps and unused labels are removed in the same
    step (cleanup_cfg); the rewrite alone would not lower the cost."""
    out = []
    for i, ins in enumerate(program):
        if ins.op == "ifz" and is_const(ins.a):
            if ins.a == 0:
                new = _replace_at(program, i, Instr("goto", target=ins.target))
                what = f"always jumps -> goto {ins.target}"
            else:
                new = _delete_at(program, i)
                what = "never jumps -> removed"
            out.append(Mutation("M10", f"[{i}] {ins}: {what}, unreachable code removed", cleanup_cfg(new)))
    return out


# MX (unsafe, demo only) --------------------------------------------------------
def unchecked_reordering(program, live_out=NO_LIVE):
    """Swaps ANY adjacent pair with no dependence check. Used with --unsafe to
    demonstrate that the validator rejects semantically wrong candidates."""
    out = []
    for i in range(len(program) - 1):
        x, y = program[i], program[i + 1]
        if not _independent(x, y) and x.op not in CONTROL_OPS and y.op not in CONTROL_OPS:
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
    "M10": "Branch Folding (CFG)",
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
    ("M10", branch_folding),
]
UNSAFE_OPERATORS = [("MX", unchecked_reordering)]
GLOBAL_OPERATORS = frozenset({"M10"})  # operate on the whole CFG, not on one block


def generate_mutations(program, operators=OPERATORS):
    if not has_control_flow(program):
        candidates = []
        for op_id, fn in operators:
            if op_id not in GLOBAL_OPERATORS:
                candidates.extend(fn(program))
        return candidates
    candidates = []
    for blk in basic_blocks(program):
        lo, hi = blk.body_start, blk.end  # body + terminator, label excluded
        if lo >= hi:
            continue
        prefix, body, suffix = program[:lo], program[lo:hi], program[hi:]
        live_out = frozenset(v for ins in prefix + suffix for v in ins.uses())
        for op_id, fn in operators:
            if op_id in GLOBAL_OPERATORS:
                continue
            for m in fn(body, live_out):
                candidates.append(Mutation(m.op_id, f"B{blk.index}: {m.description}",
                                           prefix + m.program + suffix))
    for op_id, fn in operators:
        if op_id in GLOBAL_OPERATORS:
            candidates.extend(fn(program))
    return candidates
