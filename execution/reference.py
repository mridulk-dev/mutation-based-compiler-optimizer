"""Reference semantics: evaluate the source AST directly, without TAC.

This is the semantic oracle for dataset expected outputs and end-to-end checks.
It is deliberately independent of the TAC generator, the TAC interpreter and the
optimiser: it walks the AST and implements the arithmetic itself (it does NOT
import `apply_op` / `c_div` from execution.interpreter), so a bug in the shared
TAC arithmetic cannot silently make both sides agree.

Language semantics implemented here (MiniArith specification):
  * integers are unbounded (Python int); 32-bit overflow is not modelled, but
    `int32_violations` reports when an intermediate leaves the C int range;
  * `/` truncates toward zero, `%` has the sign of the dividend (C99), and
    a % b == a - b * (a / b);
  * `/` or `%` by zero stops the program with the error "division by zero";
    values printed before the error remain observable;
  * reading past the end of the input stream stops the program with the error
    "input stream exhausted";
  * `if (c) {..} else {..}` executes the then-branch when c != 0; relational
    operators yield 1 or 0.
"""

from dataclasses import dataclass

from frontend.parser import Assign, BinOp, If, Input, Neg, Num, Print, Var, parse

INT32_MIN, INT32_MAX = -(2 ** 31), 2 ** 31 - 1

DIV_ZERO = "division by zero"
INPUT_EXHAUSTED = "input stream exhausted"


class _Stop(Exception):
    pass


def _trunc_div(a, b):
    # Independent formulation of C division: floor division, then correct the
    # result toward zero when the signs differ and the division is inexact.
    if b == 0:
        raise _Stop(DIV_ZERO)
    q = a // b
    if q < 0 and q * b != a:
        q += 1
    return q


def _trunc_mod(a, b):
    if b == 0:
        raise _Stop(DIV_ZERO)
    r = abs(a) % abs(b)
    return -r if a < 0 else r


_REL = {
    "<": lambda a, b: int(a < b),
    "<=": lambda a, b: int(a <= b),
    ">": lambda a, b: int(a > b),
    ">=": lambda a, b: int(a >= b),
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
}


@dataclass(frozen=True)
class RefResult:
    outputs: tuple
    error: object          # None or an error message
    int32_violations: int  # intermediate values outside the C int range
    max_abs: int = 0       # largest magnitude of any computed value

    @property
    def observable(self):
        return (self.outputs, self.error)


def ast_run_detailed(ast, inputs):
    env, out, stream = {}, [], iter(inputs)
    violations = [0, 0]  # [int32 violations, max |value|]

    def chk(v):
        if v < INT32_MIN or v > INT32_MAX:
            violations[0] += 1
        if abs(v) > violations[1]:
            violations[1] = abs(v)
        return v

    def ev(node):
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Var):
            return env[node.name]
        if isinstance(node, Neg):
            return chk(-ev(node.operand))
        if isinstance(node, BinOp):
            a, b = ev(node.left), ev(node.right)
            op = node.op
            if op == "+":
                return chk(a + b)
            if op == "-":
                return chk(a - b)
            if op == "*":
                return chk(a * b)
            if op == "/":
                return chk(_trunc_div(a, b))
            if op == "%":
                return _trunc_mod(a, b)
            if op in _REL:
                return _REL[op](a, b)
            raise TypeError(f"unknown operator {op!r}")
        raise TypeError(node)

    def ex(stmts):
        for stmt in stmts:
            if isinstance(stmt, Input):
                for name in stmt.names:
                    try:
                        env[name] = next(stream)
                    except StopIteration:
                        raise _Stop(INPUT_EXHAUSTED) from None
            elif isinstance(stmt, Assign):
                env[stmt.name] = ev(stmt.expr)
            elif isinstance(stmt, Print):
                out.append(ev(stmt.expr))
            elif isinstance(stmt, If):
                ex(stmt.then if ev(stmt.cond) != 0 else stmt.orelse)
            else:
                raise TypeError(stmt)

    try:
        ex(ast.stmts)
    except _Stop as exc:
        return RefResult(tuple(out), str(exc), violations[0], violations[1])
    return RefResult(tuple(out), None, violations[0], violations[1])


def ast_run(ast, inputs):
    """Returns (outputs tuple, failed bool) - kept for backward compatibility."""
    r = ast_run_detailed(ast, inputs)
    return r.outputs, r.error is not None


def run_source(source, inputs):
    return ast_run(parse(source), inputs)


def run_source_detailed(source, inputs):
    return ast_run_detailed(parse(source), inputs)
