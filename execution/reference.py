"""Reference semantics: evaluate the source AST directly, without TAC.

This is independent of the TAC generator and optimiser, so it serves as the
oracle for expected outputs in the dataset and for end-to-end checks.
"""

from frontend.parser import Assign, BinOp, Input, Neg, Num, Print, Var, parse

from .interpreter import ExecutionError, apply_op


def ast_run(ast, inputs):
    """Returns (outputs tuple, failed bool)."""
    env, out, stream = {}, [], iter(inputs)

    def ev(node):
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Var):
            return env[node.name]
        if isinstance(node, Neg):
            return -ev(node.operand)
        if isinstance(node, BinOp):
            return apply_op(node.op, ev(node.left), ev(node.right))
        raise TypeError(node)

    try:
        for stmt in ast.stmts:
            if isinstance(stmt, Input):
                for name in stmt.names:
                    try:
                        env[name] = next(stream)
                    except StopIteration:
                        raise ExecutionError("input stream exhausted") from None
            elif isinstance(stmt, Assign):
                env[stmt.name] = ev(stmt.expr)
            elif isinstance(stmt, Print):
                out.append(ev(stmt.expr))
    except ExecutionError:
        return tuple(out), True
    return tuple(out), False


def run_source(source, inputs):
    return ast_run(parse(source), inputs)
