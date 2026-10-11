"""Language semantics: the AST reference, the TAC interpreter and the compiled
backend must implement the same MiniArith specification, independently."""

import ast
import inspect
import itertools
import unittest

import execution.reference as reference
from execution.interpreter import run
from execution.pycodegen import compile_tac
from execution.reference import ast_run_detailed, run_source_detailed
from frontend.lexer import LexError
from frontend.parser import ParseError, parse
from frontend.tac import compile_source


def three_way(source, inputs):
    """(reference, tac interpreter, compiled) observable behaviour."""
    ref = run_source_detailed(source, inputs).observable
    program = compile_source(source)
    tac = run(program, inputs).observable
    comp = compile_tac(program)(inputs)
    return ref, tac, comp


class ArithmeticSemanticsTests(unittest.TestCase):
    def test_c_truncating_division_and_modulo(self):
        cases = {(-7, 2): (-3, -1), (7, -2): (-3, 1), (-7, -2): (3, -1), (7, 2): (3, 1),
                 (0, 5): (0, 0), (0, -5): (0, 0), (-1, 3): (0, -1), (1, -3): (0, 1), (-6, 3): (-2, 0)}
        for (a, b), expected in cases.items():
            with self.subTest(a=a, b=b):
                ref, tac, comp = three_way("input a, b; print a / b; print a % b;", [a, b])
                self.assertEqual(ref, (expected, None))
                self.assertEqual(tac, ref)
                self.assertEqual(comp, ref)

    def test_division_identity_holds_on_grid(self):
        # a == b * (a / b) + a % b for every non-zero b, all three backends agree
        src = "input a, b; q = a / b; r = a % b; print q; print r; print b * q + r - a;"
        for a, b in itertools.product(range(-9, 10), repeat=2):
            ref, tac, comp = three_way(src, [a, b])
            self.assertEqual(tac, ref, (a, b))
            self.assertEqual(comp, ref, (a, b))
            if b:
                self.assertEqual(ref[0][2], 0)
            else:
                self.assertEqual(ref, ((), "division by zero"))

    def test_large_values_are_exact(self):
        big = 10 ** 30
        ref, tac, comp = three_way("input a, b; print a / b; print a % b; print a * b;", [-big - 7, 3])
        self.assertEqual(ref[0][0], -((big + 7) // 3))
        self.assertEqual(tac, ref)
        self.assertEqual(comp, ref)

    def test_negative_and_zero_operands(self):
        ref, tac, _ = three_way("input a; print -a; print -(-a); print 0 - a; print a * 0; print 0 * -a;", [-5])
        self.assertEqual(ref, ((5, -5, 5, 0, 0), None))
        self.assertEqual(tac, ref)


class ErrorSemanticsTests(unittest.TestCase):
    def test_division_by_zero_keeps_earlier_output(self):
        for op in ("/", "%"):
            with self.subTest(op=op):
                ref, tac, comp = three_way(f"input a, b; print a; print a {op} b; print b;", [4, 0])
                self.assertEqual(ref, ((4,), "division by zero"))
                self.assertEqual(tac, ref)
                self.assertEqual(comp, ref)

    def test_constant_division_by_zero_is_runtime_error_not_compile_error(self):
        ref, tac, _ = three_way("print 1; x = 5 / 0; print 2;", [])
        self.assertEqual(ref, ((1,), "division by zero"))
        self.assertEqual(tac, ref)

    def test_dead_division_by_zero_still_fails(self):
        # the quotient is never used, but the error is observable behaviour
        ref, tac, _ = three_way("input a; unused = 10 / a; print a;", [0])
        self.assertEqual(ref, ((), "division by zero"))
        self.assertEqual(tac, ref)

    def test_input_exhaustion(self):
        ref, tac, comp = three_way("input a, b; print a;", [1])
        self.assertEqual(ref, ((), "input stream exhausted"))
        self.assertEqual(tac, ref)
        self.assertEqual(comp, ref)

    def test_tac_undefined_variable_is_error_not_crash(self):
        from frontend.tac import parse_tac
        r = run(parse_tac("print x"), [])
        self.assertIn("undefined variable", r.error)

    def test_tac_jump_to_missing_label_is_error_not_crash(self):
        from frontend.tac import parse_tac
        r = run(parse_tac("goto L9\nprint 1"), [])
        self.assertIn("undefined label", r.error)


class OrderingAndRedefinitionTests(unittest.TestCase):
    def test_output_order_and_redefinition(self):
        src = "input a; x = a + 1; print x; x = x * 2; print x; a = 7; print a + x;"
        ref, tac, comp = three_way(src, [3])
        self.assertEqual(ref, ((4, 8, 15), None))
        self.assertEqual(tac, ref)
        self.assertEqual(comp, ref)

    def test_input_order(self):
        ref, _, _ = three_way("input a; input b, c; print a - b * c;", [10, 2, 3])
        self.assertEqual(ref, ((4,), None))


class ReferenceIndependenceTests(unittest.TestCase):
    def test_reference_does_not_import_tac_arithmetic(self):
        tree = ast.parse(inspect.getsource(reference))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(("." * node.level) + (node.module or ""))
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        self.assertEqual(imported & {"apply_op", "c_div", "c_mod", ".interpreter", "execution.interpreter",
                                     "frontend.tac", "optimizer"}, set())
        self.assertLessEqual({m for m in imported if "." in m and not m.startswith(".")}, {"frontend.parser"})

    def test_int32_violation_tracking(self):
        r = ast_run_detailed(parse("input a; print a * a;"), [70000])
        self.assertEqual(r.int32_violations, 1)
        self.assertEqual(ast_run_detailed(parse("input a; print a * a;"), [100]).int32_violations, 0)


class ControlFlowSemanticsTests(unittest.TestCase):
    SRC = """input a, b;
if (a < b) { m = a; } else { m = b; }
if (a - b) { print 1; }
print m;
"""

    def test_if_else_three_way(self):
        for a, b in itertools.product(range(-3, 4), repeat=2):
            ref, tac, _ = three_way_cf(self.SRC, [a, b])
            expected = ((1, min(a, b)) if a != b else (min(a, b),), None)
            self.assertEqual(ref, expected)
            self.assertEqual(tac, ref)

    def test_relational_operators(self):
        for op, fn in (("<", lambda a, b: a < b), ("<=", lambda a, b: a <= b), (">", lambda a, b: a > b),
                       (">=", lambda a, b: a >= b), ("==", lambda a, b: a == b), ("!=", lambda a, b: a != b)):
            src = f"input a, b; if (a {op} b) {{ print 1; }} else {{ print 0; }}"
            for a, b in ((1, 2), (2, 2), (3, 2)):
                ref, tac, _ = three_way_cf(src, [a, b])
                self.assertEqual(ref, ((int(fn(a, b)),), None))
                self.assertEqual(tac, ref)

    def test_nested_if_and_error_inside_branch(self):
        src = "input a; if (a) { if (a > 5) { print 10 / (a - 6); } else { print 2; } } print 3;"
        for a, exp in ((0, ((3,), None)), (1, ((2, 3), None)), (6, ((), "division by zero")), (8, ((5, 3), None))):
            ref, tac, _ = three_way_cf(src, [a])
            self.assertEqual(ref, exp)
            self.assertEqual(tac, ref)

    def test_definite_assignment(self):
        with self.assertRaises(ParseError):
            parse("input a; if (a) { y = 1; } print y;")
        with self.assertRaises(ParseError):
            parse("input a; if (a) { y = 1; } else { z = 2; } print y;")
        parse("input a; if (a) { y = 1; } else { y = 2; } print y;")

    def test_malformed_control_flow(self):
        for bad in ("input a; if a { print a; }", "input a; if (a) print a;", "input a; if (a) { print a;",
                    "input a; else { print a; }", "input a; if (a < ) { print a; }"):
            with self.subTest(src=bad), self.assertRaises((ParseError, LexError)):
                parse(bad)

    def test_relational_only_in_conditions(self):
        with self.assertRaises(ParseError):
            parse("input a; x = a < 3; print x;")


def three_way_cf(source, inputs):
    ref = run_source_detailed(source, inputs).observable
    tac = run(compile_source(source), inputs).observable
    return ref, tac, None


if __name__ == "__main__":
    unittest.main()
