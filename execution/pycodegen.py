"""Compile straight-line TAC to a Python function (second execution backend).

Used for two things:
  * wall-clock timing without the TAC interpreter's per-instruction dispatch
    overhead, so measured time tracks the operations actually executed, and
  * an extra differential check: compiled code must agree with the TAC
    interpreter and the AST reference.

Only straight-line TAC is supported (Python has no goto); `compile_tac` raises
ValueError for programs with labels or jumps. Division semantics follow the
language spec (truncate toward zero, error "division by zero").
"""

from frontend.tac import CONTROL_OPS, is_var

_PY_OP = {"+": "+", "-": "-", "*": "*", "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==", "!=": "!="}


class CompiledError(Exception):
    pass


def _div(a, b):
    if b == 0:
        raise CompiledError("division by zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _mod(a, b):
    return a - b * _div(a, b)


def _shl(a, b):
    if b < 0:
        raise CompiledError("negative shift")
    return a << b


def _name(operand):
    return f"v_{operand}" if is_var(operand) else repr(operand)


def tac_to_python(program):
    if any(ins.op in CONTROL_OPS for ins in program):
        raise ValueError("compiled backend supports straight-line TAC only")
    lines = ["def _prog(_inputs, _out):", "    _it = iter(_inputs)"]
    for ins in program:
        a, b = _name(ins.a), _name(ins.b)
        d = f"v_{ins.dest}" if ins.dest is not None else None
        if ins.op == "input":
            lines.append(f"    {d} = next(_it)")
        elif ins.op == "print":
            lines.append(f"    _out.append({a})")
        elif ins.op == "copy":
            lines.append(f"    {d} = {a}")
        elif ins.op == "neg":
            lines.append(f"    {d} = -{a}")
        elif ins.op in ("<", "<=", ">", ">=", "==", "!="):
            lines.append(f"    {d} = int({a} {_PY_OP[ins.op]} {b})")
        elif ins.op in _PY_OP:
            lines.append(f"    {d} = {a} {_PY_OP[ins.op]} {b}")
        elif ins.op == "/":
            lines.append(f"    {d} = _div({a}, {b})")
        elif ins.op == "%":
            lines.append(f"    {d} = _mod({a}, {b})")
        elif ins.op == "<<":
            lines.append(f"    {d} = _shl({a}, {b})")
        else:
            raise ValueError(f"unsupported op {ins.op!r}")
    lines.append("    return _out")
    return "\n".join(lines) + "\n"


def compile_tac(program):
    """Returns f(inputs) -> (outputs tuple, error or None)."""
    namespace = {"_div": _div, "_mod": _mod, "_shl": _shl}
    exec(compile(tac_to_python(program), "<tac>", "exec"), namespace)  # noqa: S102 - generated from validated TAC only
    prog = namespace["_prog"]

    def call(inputs):
        out = []
        try:
            prog(inputs, out)
        except CompiledError as exc:
            return tuple(out), str(exc)
        except StopIteration:
            return tuple(out), "input stream exhausted"
        return tuple(out), None

    call.raw = prog
    return call
