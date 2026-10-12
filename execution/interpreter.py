"""TAC interpreter with a deterministic cycle model.

Execution time is measured in simulated cycles rather than wall-clock time so
that cost comparisons are reproducible and not drowned out by OS noise.

Cycle model (simple in-order pipeline):
  * each instruction costs CYCLES[op]
  * reading a value produced by the immediately preceding instruction costs
    STALL_CYCLES extra (read-after-write hazard / result latency).
The stall term is what makes instruction reordering (M8) worthwhile.
Control flow (staged extension): `label` is a pseudo-instruction costing 0
cycles; `goto` and `ifz` cost 1 cycle. "Preceding instruction" means the
previously *executed* instruction.
"""

from dataclasses import dataclass

from frontend.tac import is_var

CYCLES = {
    "input": 1,
    "print": 1,
    "copy": 1,
    "neg": 1,
    "+": 1,
    "-": 1,
    "<<": 1,
    "*": 3,
    "/": 10,
    "%": 10,
    "<": 1,
    "<=": 1,
    ">": 1,
    ">=": 1,
    "==": 1,
    "!=": 1,
    "goto": 1,
    "ifz": 1,
    "label": 0,
}
MAX_STEPS = 1_000_000  # guard: MiniArith has no loops, so this is never reached by valid code
STALL_CYCLES = 1


class ExecutionError(Exception):
    pass


def c_div(a, b):
    """Integer division truncating toward zero (C semantics)."""
    if b == 0:
        raise ExecutionError("division by zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def c_mod(a, b):
    return a - b * c_div(a, b)


def apply_op(op, a, b=None):
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return c_div(a, b)
    if op == "%":
        return c_mod(a, b)
    if op == "<<":
        if b < 0:
            raise ExecutionError("negative shift")
        return a << b
    if op == "neg":
        return -a
    if op == "<":
        return int(a < b)
    if op == "<=":
        return int(a <= b)
    if op == ">":
        return int(a > b)
    if op == ">=":
        return int(a >= b)
    if op == "==":
        return int(a == b)
    if op == "!=":
        return int(a != b)
    raise ExecutionError(f"unknown operator {op!r}")


@dataclass(frozen=True)
class RunResult:
    outputs: tuple
    error: object  # None or error message string
    cycles: int

    @property
    def observable(self):
        """What the user can observe: printed values plus whether/how it failed."""
        return (self.outputs, self.error)


def _label_map(program):
    labels = {}
    for i, ins in enumerate(program):
        if ins.op == "label":
            labels[ins.target] = i
    return labels


def _jump(labels, target):
    if target not in labels:
        raise ExecutionError(f"jump to undefined label {target!r}")
    return labels[target]


def run(program, inputs):
    env = {}
    outputs = []
    stream = iter(inputs)
    cycles = 0
    prev_dest = None
    labels = _label_map(program) if any(ins.op == "label" for ins in program) else {}

    def value(operand):
        if is_var(operand):
            if operand not in env:
                raise ExecutionError(f"read of undefined variable {operand!r}")
            return env[operand]
        return operand

    pc, n, steps = 0, len(program), 0
    try:
        while pc < n:
            ins = program[pc]
            pc += 1
            op = ins.op
            if op == "label":
                continue
            steps += 1
            if steps > MAX_STEPS:
                raise ExecutionError("step limit exceeded")
            cycles += CYCLES[op]
            if prev_dest is not None and (ins.a == prev_dest or ins.b == prev_dest):
                cycles += STALL_CYCLES
            if op == "input":
                try:
                    env[ins.dest] = next(stream)
                except StopIteration:
                    raise ExecutionError("input stream exhausted") from None
            elif op == "print":
                outputs.append(value(ins.a))
            elif op == "copy":
                env[ins.dest] = value(ins.a)
            elif op == "neg":
                env[ins.dest] = apply_op("neg", value(ins.a))
            elif op == "goto":
                pc = _jump(labels, ins.target)
            elif op == "ifz":
                if value(ins.a) == 0:
                    pc = _jump(labels, ins.target)
            else:
                env[ins.dest] = apply_op(op, value(ins.a), value(ins.b))
            prev_dest = ins.dest
    except ExecutionError as exc:
        return RunResult(tuple(outputs), str(exc), cycles)
    return RunResult(tuple(outputs), None, cycles)
