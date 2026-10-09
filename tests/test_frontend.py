import unittest

from execution.interpreter import run
from frontend.lexer import LexError, tokenize
from frontend.parser import ParseError, parse
from frontend.tac import compile_source, format_tac, parse_tac


class LexerTests(unittest.TestCase):
    def test_tokens(self):
        kinds = [t.kind for t in tokenize("input a; x = a * 2; # comment\nprint x;")]
        self.assertEqual(kinds, ["INPUT", "ID", "OP", "ID", "OP", "ID", "OP", "NUM", "OP",
                                 "PRINT", "ID", "OP", "EOF"])

    def test_rejects_temp_names_and_bad_chars(self):
        with self.assertRaises(LexError):
            tokenize("t1 = 3;")
        with self.assertRaises(LexError):
            tokenize("x = 3 $ 4;")


class ParserTests(unittest.TestCase):
    def test_undefined_variable(self):
        with self.assertRaises(ParseError):
            parse("x = y + 1;")

    def test_missing_semicolon(self):
        with self.assertRaises(ParseError):
            parse("input a\nprint a;")


class TACGenerationTests(unittest.TestCase):
    def outputs(self, source, inputs):
        return run(compile_source(source), inputs).outputs

    def test_precedence_and_associativity(self):
        self.assertEqual(self.outputs("input a; print a + 2 * 3 - 4;", [10]), (12,))
        self.assertEqual(self.outputs("print 20 - 5 - 3;", []), (12,))
        self.assertEqual(self.outputs("print (2 + 3) * 4;", []), (20,))
        self.assertEqual(self.outputs("print -3 * -(2 + 1);", []), (9,))

    def test_c_division_semantics(self):
        self.assertEqual(self.outputs("input a, b; print a / b; print a % b;", [-7, 2]), (-3, -1))

    def test_division_by_zero_is_observable_error(self):
        result = run(compile_source("input a; print 1; print a / 0;"), [5])
        self.assertEqual(result.outputs, (1,))
        self.assertIn("division by zero", result.error)

    def test_naive_codegen_shape(self):
        tac = format_tac(compile_source("input a; x = a + 1; print x;"), numbered=False)
        self.assertEqual(tac, "input a\nt1 = a + 1\nx = t1\nprint x")

    def test_tac_roundtrip(self):
        program = compile_source("input a, b; x = -(a * 3) + b % 2; print x / 1;")
        self.assertEqual(parse_tac(format_tac(program, numbered=False)), program)


if __name__ == "__main__":
    unittest.main()
