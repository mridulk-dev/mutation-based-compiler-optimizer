"""Mutation-safety tests.

Every candidate proposed by a SAFE operator must preserve observable behaviour
on its own - independently of the validator. Equivalence is checked here by
exhaustive enumeration of a small input domain (all vectors in [-4, 4]^n,
which includes 0 so division by zero is always exercised), so these tests do
not depend on the 25 validation vectors used during search.
"""

import itertools
import unittest

from execution.interpreter import run
from frontend.tac import compile_source, format_tac, parse_tac
from optimizer import mutations as m
from optimizer.mutations import OPERATORS, generate_mutations

DOMAIN = range(-4, 5)


def n_inputs(program):
    return sum(1 for i in program if i.op == "input")


def differs(a, b):
    """First input vector on which programs a and b behave differently, or None."""
    for vec in itertools.product(DOMAIN, repeat=n_inputs(a)):
        if run(a, vec).observable != run(b, vec).observable:
            return vec
    return None


HAZARD_PROGRAMS = {
    "redefinition": "input a, b; x = a + b; print x; x = x * 2; a = x - b; print a + x; x = a * 1 + 0; print x;",
    "raw_war_waw": "input a, b; x = a * 3; y = x + b; x = b - 1; z = x * y; y = a; print z; print y;",
    "io_order": "input a; print a; input b; print b * 2; c = a + b; print c; input d; print d - c;",
    "trapping_dead_division": "input a, b; q = 10 / a; r = b % a; print b; s = q + 0; print b * 2;",
    "trapping_live_division": "input a, b; print a; q = b / a; print q; print b % (a - 1);",
    "cse_invalidation": "input a, b; x = a + b; a = a + 1; y = a + b; z = b + a; print x * y; print z;",
    "cse_dest_redefined": "input a, b; x = a * b; x = x + 1; y = a * b; print x + y;",
    "copy_src_redefined": "input a, b; x = a; a = b + 1; print x; y = x + a; print y;",
    "const_prop_redefined": "input a; k = 4; x = a * k; k = a; y = a * k; print x; print y;",
    "constant_errors": "input a; x = 7 / 0 + a; print 1;",
    "constant_folding_negatives": "input a; x = -7 / 2; y = -7 % 2; z = 7 % -2; print a + x + y + z;",
    "strength_negative": "input a; x = a * 2; y = a * 8; z = 16 * a; w = -a * 4; print x + y; print z - w;",
    "chain_dead": "input a; d1 = a * 3; d2 = d1 + 4; d3 = d2 * d1; x = a + 1; print x;",
    "reorder_stalls": "input a, b; x = a * 3; y = x + 1; z = b * 5; w = z + 2; print y; print w;",
    "self_reference": "input a; a = a + 1; a = a + 1; x = a + 1; y = a + 1; print x + y;",
    "only_constants": "x = 2 * 3 + 4; y = x * 1 + 0; z = y / 3; print z % 4;",
    "unused_inputs": "input a, b, c; print b;",
}

CF_PROGRAMS = {
    "cf_live_after_join": "input a, b; x = a * 2; if (a < b) { x = b; y = 3; } else { y = 4; } print x + y;",
    "cf_dead_in_branch": "input a; if (a) { d = a * 5; x = a + 1; } else { x = a - 1; } print x;",
    "cf_trap_in_branch": "input a, b; if (b) { q = a / b; } else { q = 0; } print a; print q;",
    "cf_const_branch": "input a; k = 2; if (k > 1) { print a * 2; } else { print a; } print k + a;",
    "cf_const_false": "input a; k = 0; if (k) { x = a / k; } else { x = a + 0; } print x;",
    "cf_nested": "input a, b; if (a) { if (b) { print 1; } x = a * 4; } else { x = b; } print x * 1;",
    "cf_io_in_branches": "input a; if (a > 0) { input b; print b; } else { print a; } print a;",
}


class OperatorSoundnessTests(unittest.TestCase):
    def check_all_candidates(self, programs):
        for name, src in programs.items():
            program = compile_source(src)
            for op_id, fn in OPERATORS:
                for cand in generate_mutations(program, [(op_id, fn)]):
                    with self.subTest(program=name, operator=op_id, mutation=cand.description):
                        self.assertIsNone(differs(program, cand.program),
                                          f"\n{format_tac(program)}\n--->\n{format_tac(cand.program)}")

    def test_every_candidate_sound_straight_line(self):
        self.check_all_candidates(HAZARD_PROGRAMS)

    def test_every_candidate_sound_control_flow(self):
        self.check_all_candidates(CF_PROGRAMS)

    def test_harness_is_not_vacuous(self):
        total = sum(len(generate_mutations(compile_source(s))) for s in {**HAZARD_PROGRAMS, **CF_PROGRAMS}.values())
        self.assertGreater(total, 100)
        caught = 0
        for src in HAZARD_PROGRAMS.values():
            program = compile_source(src)
            caught += sum(differs(program, c.program) is not None for c in m.unchecked_reordering(program))
        self.assertGreater(caught, 20, "the exhaustive check must detect the unsafe operator's bugs")

    def test_two_step_candidates_sound(self):
        # soundness must not depend on starting from naive TAC
        for name, src in list(HAZARD_PROGRAMS.items())[:8]:
            program = compile_source(src)
            for first in generate_mutations(program)[:12]:
                for second in generate_mutations(first.program):
                    with self.subTest(program=name, first=first.description, second=second.description):
                        self.assertIsNone(differs(program, second.program))


class SpecificHazardTests(unittest.TestCase):
    def outs(self, fn, text, live_out=frozenset()):
        return [format_tac(c.program, numbered=False) for c in fn(parse_tac(text), live_out)]

    def test_constant_folding_never_folds_errors(self):
        self.assertEqual(self.outs(m.constant_folding, "t1 = 5 / 0\nt2 = 5 % 0\nt3 = 3 << -1"), [])

    def test_constant_folding_truncates_toward_zero(self):
        self.assertEqual(self.outs(m.constant_folding, "t1 = -7 / 2"), ["t1 = -3"])
        self.assertEqual(self.outs(m.constant_folding, "t1 = 7 % -2"), ["t1 = 1"])

    def test_cse_killed_by_operand_redefinition(self):
        self.assertEqual(self.outs(m.common_subexpression_elimination,
                                   "input a\ninput b\nt1 = a + b\nb = 1\nt2 = a + b\nprint t1\nprint t2"), [])

    def test_cse_killed_by_holder_redefinition(self):
        self.assertEqual(self.outs(m.common_subexpression_elimination,
                                   "input a\nx = a * a\nx = 3\ny = a * a\nprint x\nprint y"), [])

    def test_copy_propagation_stops_when_source_redefined(self):
        out = self.outs(m.copy_propagation, "input a\nx = a\na = 5\nprint x")
        self.assertEqual(out, [])  # nothing to substitute before a is redefined

    def test_dead_code_respects_live_out(self):
        text = "input a\nx = a + 1\ny = a * 2"
        self.assertEqual(len(self.outs(m.dead_assignment_removal, text)), 2)
        self.assertEqual(self.outs(m.dead_assignment_removal, text, frozenset({"x", "y"})), [])

    def test_reordering_never_crosses_io_or_jumps(self):
        self.assertEqual(self.outs(m.instruction_reordering, "input a\ninput b"), [])
        self.assertEqual(self.outs(m.instruction_reordering, "input a\nprint a\nprint 3"), [])
        self.assertEqual(self.outs(m.instruction_reordering, "input a\nt1 = a + 1\nifz a goto L1"), [])
        self.assertEqual(self.outs(m.instruction_reordering, "input a\nt1 = 4 / a\nprint 1"), [])

    def test_reordering_respects_dependencies(self):
        self.assertEqual(self.outs(m.instruction_reordering, "input a\nx = a + 1\ny = x * 2"), [])  # RAW
        self.assertEqual(self.outs(m.instruction_reordering, "input a\ny = a + 1\na = 3"), [])     # WAR
        self.assertEqual(self.outs(m.instruction_reordering, "input b\nx = b + 1\nx = 3"), [])     # WAW

    def test_strength_reduction_correct_for_negatives(self):
        for k in (2, 4, 8, 16, 1024):
            prog = parse_tac(f"input a\nt1 = a * {k}\nprint t1")
            (cand,) = m.strength_reduction(prog)
            for a in range(-50, 51):
                self.assertEqual(run(cand.program, [a]).outputs, (a * k,))

    def test_branch_folding_removes_dead_branch(self):
        folded = parse_tac("input a\nifz 1 goto L1\nprint a\ngoto L2\nlabel L1\nprint 0\nlabel L2\nprint 2")
        (cand,) = m.branch_folding(folded)
        self.assertEqual(format_tac(cand.program, numbered=False), "input a\nprint a\nprint 2")
        folded0 = parse_tac("input a\nifz 0 goto L1\nprint a\ngoto L2\nlabel L1\nprint 0\nlabel L2\nprint 2")
        (cand0,) = m.branch_folding(folded0)
        self.assertEqual(format_tac(cand0.program, numbered=False), "input a\nprint 0\nprint 2")

    def test_block_local_operators_never_touch_labels(self):
        prog = compile_source(CF_PROGRAMS["cf_nested"])
        labels = [i for i in prog if i.op in ("label", "goto", "ifz")]
        for cand in generate_mutations(prog):
            if cand.op_id == "M10":
                continue
            self.assertEqual([i for i in cand.program if i.op in ("label", "goto")],
                             [i for i in labels if i.op in ("label", "goto")], cand.description)


if __name__ == "__main__":
    unittest.main()
