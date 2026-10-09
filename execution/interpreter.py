"""TAC interpreter with a deterministic cycle model.

Execution time is measured in simulated cycles rather than wall-clock time so
that cost comparisons are reproducible and not drowned out by OS noise.

Cycle model (simple in-order pipeline):
  * each instruction costs CYCLES[op]
  * reading a value produced by the immediately preceding instruction costs
    STALL_CYCLES extra (read-after-write hazard / result latency).
The stall term is what makes instruction reordering (M8) worthwhile.
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
}
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


def run(program, inputs):
    env = {}
    outputs = []
    stream = iter(inputs)
    cycles = 0
    prev_dest = None

    def value(operand):
        if is_var(operand):
            if operand not in env:
                raise ExecutionError(f"read of undefined variable {operand!r}")
            return env[operand]
        return operand

    try:
        for ins in program:
            cycles += CYCLES[ins.op]
            if prev_dest is not None and prev_dest in ins.uses():
                cycles += STALL_CYCLES
            if ins.op == "input":
                try:
                    env[ins.dest] = next(stream)
                except StopIteration:
                    raise ExecutionError("input stream exhausted") from None
            elif ins.op == "print":
                outputs.append(value(ins.a))
            elif ins.op == "copy":
                env[ins.dest] = value(ins.a)
            elif ins.op == "neg":
                env[ins.dest] = apply_op("neg", value(ins.a))
            else:
                env[ins.dest] = apply_op(ins.op, value(ins.a), value(ins.b))
            prev_dest = ins.dest
    except ExecutionError as exc:
        return RunResult(tuple(outputs), str(exc), cycles)
    return RunResult(tuple(outputs), None, cycles)
