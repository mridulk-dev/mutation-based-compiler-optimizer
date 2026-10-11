"""Group C: conservative extraction of MiniArith fragments from external C code.

Source collection: jotai_unique_300/ (300 byte-distinct files from the Jotai
benchmark suite, anghaLeaves). Jotai functions were taken from AnghaBench, which
mined them from open-source GitHub projects.

A function is translated ONLY if it falls entirely inside this C subset:

    int NAME(int p1, ..., int pk) {          # return type and params exactly `int`
        int x;  int y = e;  x = e;  x op= e; # op in + - * / %
        return e;                            # exactly one return, last statement
    }
    e := decimal/hex/octal literal <= INT_MAX (no suffix) | identifier
       | -e | +e | (e) | e (+|-|*|/|%) e

Anything else (pointers, arrays, structs, globals, casts, calls, loops, branches,
ternaries, comparisons, bitwise/shift ops, ++/--, unsigned/long/char types,
uninitialised reads, multiple functions) makes the file INCOMPATIBLE, and the
reason is recorded. No partial or "approximate" translations are produced.

Translation (C -> MiniArith): parameters become one `input` statement in order,
declarations-with-initialiser and assignments become assignments, `x op= e`
becomes `x = x op (e)`, and `return e` becomes `print e`. Identifiers that clash
with MiniArith reserved words or the temporary pattern t<digits> get a `c_` prefix.

Semantic caveat: C `int` is 32-bit and signed overflow is undefined behaviour,
while MiniArith integers are unbounded. Test vectors whose evaluation leaves the
int32 range at any point are therefore discarded; the fragment is equivalent to
the C function only on inputs where the C function has defined behaviour.
Division by zero is UB in C but a defined error in MiniArith; such vectors are
kept as MiniArith tests but never run through C.

When gcc is available the translation is cross-checked against the *unmodified*
upstream code: (1) the original Jotai file is compiled and run on its own
harness cases whose inputs are literals; (2) the extracted function text is
compiled verbatim into a harness and run on every int32-safe, error-free vector.
"""

import csv
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

from execution.reference import INT32_MAX, run_source_detailed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOTAI_DIR = os.path.join(ROOT, "jotai_unique_300")
COLLECTION = {
    "name": "Jotai benchmarks (anghaLeaves subset)",
    "url": "https://github.com/lac-dcc/jotai-benchmarks",
    "repository_license": "GPL-3.0",
    "repository_license_status": "verified at repository level (GitHub repository page, checked 2026-10-12)",
    "upstream_origin": "Functions taken from AnghaBench, mined from open-source GitHub projects",
    "citation": "C. C. Kind, M. Canesche, F. M. Q. Pereira. Jotai: a Methodology for the Generation of "
                "Executable C Benchmarks. Tech. Report 02-2022, Universidade Federal de Minas Gerais, 2022.",
}
EXTRACTOR_VERSION = "1.0"
RESERVED = {"input", "print", "if", "else"}

_TOKEN_RE = re.compile(r"""
    (?P<num>0[xX][0-9a-fA-F]+[uUlL]*|\d+[uUlL]*)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<op>\+=|-=|\*=|/=|%=|\+\+|--|->|<<=|>>=|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%=(){}\[\];,<>!~&|^?:.])
  | (?P<ws>\s+)
  | (?P<other>.)
""", re.X)

UNSUPPORTED_KEYWORDS = {
    "if": "branch", "else": "branch", "switch": "branch", "case": "branch", "for": "loop", "while": "loop",
    "do": "loop", "goto": "goto", "break": "control", "continue": "control", "sizeof": "sizeof",
    "unsigned": "unsigned type", "long": "long type", "short": "short type", "char": "char type",
    "float": "floating point", "double": "floating point", "struct": "struct", "union": "union",
    "enum": "enum", "void": "void type", "const": "qualifier", "volatile": "qualifier",
}
UNSUPPORTED_OPS = {
    "->": "pointer/struct access", ".": "struct access", "[": "array access", "]": "array access",
    "&": "bitwise/address-of", "|": "bitwise", "^": "bitwise", "~": "bitwise", "<<": "shift", ">>": "shift",
    "<<=": "shift", ">>=": "shift", "?": "ternary", ":": "ternary", "!": "logical", "&&": "logical",
    "||": "logical", "<": "comparison", ">": "comparison", "<=": "comparison", ">=": "comparison",
    "==": "comparison", "!=": "comparison", "++": "increment", "--": "decrement", "{": "nested block",
    "}": "nested block",
}


class Incompatible(Exception):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def function_region(text):
    start = text.find("/* Variables and functions */")
    end = text.find("// ----", start)
    if start < 0 or end < 0:
        raise Incompatible("unrecognised Jotai file layout")
    return text[start + len("/* Variables and functions */"):end].strip()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def tokenize_c(text):
    toks = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind == "ws":
            continue
        toks.append((kind, m.group()))
    return toks


def _strip_attributes(toks):
    out, i = [], 0
    while i < len(toks):
        if toks[i] == ("id", "__attribute__"):
            depth, i = 0, i + 1
            while i < len(toks):
                if toks[i][1] == "(":
                    depth += 1
                elif toks[i][1] == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            continue
        out.append(toks[i])
        i += 1
    return out


def _literal(text):
    if re.search(r"[uUlL]$", text):
        raise Incompatible("integer literal with unsigned/long suffix")
    if text.lower().startswith("0x"):
        value = int(text, 16)
    elif len(text) > 1 and text.startswith("0"):
        value = int(text, 8)
    else:
        value = int(text)
    if value > INT32_MAX:
        raise Incompatible("integer literal does not fit in int")
    return value


class _ExprParser:
    def __init__(self, toks, scope, rename):
        self.toks, self.pos, self.scope, self.rename = toks, 0, scope, rename

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else ("eof", "")

    def take(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def parse(self):
        out = self.expr()
        if self.pos != len(self.toks):
            raise Incompatible(f"unexpected token {self.peek()[1]!r} in expression")
        return out

    def expr(self):
        node = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = f"{node} {op} {self.term()}"
        return node

    def term(self):
        node = self.unary()
        while self.peek()[1] in ("*", "/", "%"):
            op = self.take()[1]
            node = f"{node} {op} {self.unary()}"
        return node

    def unary(self):
        if self.peek()[1] == "-":
            self.take()
            return f"-{self.unary()}"
        if self.peek()[1] == "+":
            self.take()
            return self.unary()
        return self.atom()

    def atom(self):
        kind, text = self.take()
        if kind == "num":
            return str(_literal(text))
        if kind == "id":
            if text in UNSUPPORTED_KEYWORDS:
                raise Incompatible(UNSUPPORTED_KEYWORDS[text])
            if self.peek()[1] == "(":
                raise Incompatible("function call")
            if text not in self.scope:
                raise Incompatible("reads global or undeclared variable")
            if not self.scope[text]:
                raise Incompatible("reads uninitialised local variable")
            return self.rename(text)
        if text == "(":
            if self.peek()[0] == "id" and self.peek()[1] in ("int", *UNSUPPORTED_KEYWORDS):
                raise Incompatible("cast")
            inner = self.expr()
            if self.take()[1] != ")":
                raise Incompatible("unbalanced parentheses")
            return f"({inner})"
        if text in UNSUPPORTED_OPS:
            raise Incompatible(UNSUPPORTED_OPS[text])
        raise Incompatible(f"unexpected token {text!r}")


def _split_top(toks, sep):
    parts, cur, depth = [], [], 0
    for tok in toks:
        if tok[1] == "(":
            depth += 1
        elif tok[1] == ")":
            depth -= 1
        if tok[1] == sep and depth == 0:
            parts.append(cur)
            cur = []
        else:
            cur.append(tok)
    parts.append(cur)
    return parts


def translate_function(region):
    """C function region -> (function name, params, MiniArith source lines).
    Raises Incompatible with a human-readable reason."""
    toks = _strip_attributes(tokenize_c(strip_comments(region)))
    opens = [i for i, t in enumerate(toks) if t[1] == "{"]
    if not opens:
        raise Incompatible("no function definition")
    # the function starts after the last ';' before its body; anything earlier is
    # a global declaration, which the function is not allowed to reference
    semis = [i for i in range(opens[0]) if toks[i][1] == ";"]
    toks = toks[(semis[-1] + 1 if semis else 0):]
    for kind, text in toks:  # fast, specific rejection reasons first
        if kind == "other":
            raise Incompatible(f"unsupported character {text!r} in function")
        if text in UNSUPPORTED_OPS and text not in ("{", "}"):
            raise Incompatible(UNSUPPORTED_OPS[text])
        if kind == "id" and text in UNSUPPORTED_KEYWORDS and text not in ("void",):
            raise Incompatible(UNSUPPORTED_KEYWORDS[text])
    opens = [i for i, t in enumerate(toks) if t[1] == "{"]
    if len(opens) != 1 or [t[1] for t in toks].count("}") != 1 or toks[-1][1] != "}":
        raise Incompatible("not exactly one function with a flat body")
    brace = opens[0]
    header, body = toks[:brace], toks[brace + 1:-1]
    if header.count(("op", "(")) != 1 or header.count(("op", ")")) != 1 or header[-1][1] != ")":
        raise Incompatible("unrecognised function header")
    lp = header.index(("op", "("))
    pre, params_toks = header[:lp], header[lp + 1:-1]
    quals = [t[1] for t in pre[:-1] if t[1] not in ("static", "inline", "extern")]
    if not pre or pre[-1][0] != "id":
        raise Incompatible("unrecognised function header")
    fname = pre[-1][1]
    if quals != ["int"]:
        raise Incompatible(f"return type {' '.join(quals) or '?'} (only int supported)")
    params = []
    if params_toks and params_toks != [("id", "void")]:
        for p in _split_top(params_toks, ","):
            if len(p) != 2 or p[0][1] != "int" or p[1][0] != "id":
                raise Incompatible("parameter type other than int")
            params.append(p[1][1])
    if any(t == ("id", "void") for t in body):
        raise Incompatible("void type")

    def rename(name):
        return f"c_{name}" if name in RESERVED or re.fullmatch(r"t\d+", name) else name

    scope = {p: True for p in params}
    lines = [f"input {', '.join(rename(p) for p in params)};"] if params else []
    stmts = _split_top(body, ";")
    if stmts and stmts[-1]:
        raise Incompatible("missing ';' after last statement")
    stmts = [s for s in stmts if s]
    if not stmts or stmts[-1][0] != ("id", "return"):
        raise Incompatible("function does not end with a return statement")
    for k, st in enumerate(stmts):
        head = st[0]
        if head == ("id", "return"):
            if k != len(stmts) - 1:
                raise Incompatible("early return")
            lines.append(f"print {_ExprParser(st[1:], scope, rename).parse()};")
        elif head == ("id", "int"):
            for decl in _split_top(st[1:], ","):
                if not decl or decl[0][0] != "id":
                    raise Incompatible("unsupported declaration")
                name = decl[0][1]
                if len(decl) == 1:
                    scope[name] = False
                elif decl[1][1] == "=":
                    value = _ExprParser(decl[2:], scope, rename).parse()
                    scope[name] = True
                    lines.append(f"{rename(name)} = {value};")
                else:
                    raise Incompatible("unsupported declaration")
        elif head[0] == "id" and len(st) > 2 and st[1][1] in ("=", "+=", "-=", "*=", "/=", "%="):
            name, op = head[1], st[1][1]
            if name not in scope:
                raise Incompatible("writes global or undeclared variable")
            value = _ExprParser(st[2:], scope, rename).parse()
            if op != "=":
                if not scope[name]:
                    raise Incompatible("reads uninitialised local variable")
                value = f"{rename(name)} {op[0]} ({value})"
            scope[name] = True
            lines.append(f"{rename(name)} = {value};")
        else:
            raise Incompatible("unsupported statement")
    return fname, params, lines


def upstream_literal_cases(text, params):
    """Harness cases in the Jotai main() whose parameters are all int literals.
    Returns [(case_number, [values...])]."""
    cases = []
    for m in re.finditer(r"case\s+(\d+)\s*:\s*\{(.*?)\bbreak;", text, flags=re.S):
        body = m.group(2)
        values = []
        for p in params:
            mm = re.search(rf"\bint\s+{re.escape(p)}\s*=\s*(-?\d+)\s*;", body)
            if not mm:
                break
            values.append(int(mm.group(1)))
        else:
            cases.append((int(m.group(1)), values))
    return cases


def find_gcc():
    return shutil.which("gcc")


def _gcc_run(gcc, c_source, args_list, workdir):
    src = os.path.join(workdir, "prog.c")
    exe = os.path.join(workdir, "prog.exe")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(c_source)
    proc = subprocess.run([gcc, "-O0", "-w", "-o", exe, src], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return None, proc.stderr.strip()[:300]
    outs = []
    for args in args_list:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=30)
        outs.append(r.stdout.strip())
    return outs, None


def gcc_crosscheck(gcc, original_text, region, fname, params, upstream_cases, vectors, expected):
    """Returns dict with status 'passed' | 'failed' | 'error' and details.
    (1) the unmodified upstream file on its literal harness cases - this may not
    build on every platform, since the Jotai header targets Linux (e.g. MinGW
    rejects its size_t typedef); (2) the extracted function text, verbatim, in a
    minimal harness on every error-free int32-safe vector. (2) must pass."""
    detail = {"compiler": "gcc", "upstream_file_built": False, "upstream_cases_checked": 0,
              "harness_vectors_checked": 0}
    with tempfile.TemporaryDirectory() as wd:
        if upstream_cases:
            outs, err = _gcc_run(gcc, original_text, [[str(c)] for c, _ in upstream_cases], wd)
            if err:
                first = next((ln for ln in err.splitlines() if "error" in ln), err.splitlines()[0])
                detail["upstream_file_note"] = "did not build with this gcc: " + first.split("error:")[-1].strip()
            else:
                detail["upstream_file_built"] = True
                for (case, vals), out in zip(upstream_cases, outs):
                    exp = expected.get(tuple(vals))
                    if exp is None:
                        continue
                    detail["upstream_cases_checked"] += 1
                    if out != str(exp):
                        return {**detail, "status": "failed", "message": f"case {case} {vals}: C={out} ref={exp}"}
        calls = [v for v in vectors if expected.get(tuple(v)) is not None]
        if not calls:
            return {**detail, "status": "error", "message": "no error-free vectors to check"}
        body = "\n".join(f'    printf("%d\\n", {fname}({", ".join(map(str, v))}));' for v in calls)
        harness = f"#include <stdio.h>\n{region}\n\nint main(void) {{\n{body}\n    return 0;\n}}\n"
        outs, err = _gcc_run(gcc, harness, [[]], wd)
        if err:
            return {**detail, "status": "error", "message": f"harness did not compile: {err[:200]}"}
        got = outs[0].split()
        detail["harness_vectors_checked"] = len(calls)
        if len(got) != len(calls):
            return {**detail, "status": "failed", "message": "harness output length mismatch"}
        for v, g in zip(calls, got):
            if g != str(expected[tuple(v)]):
                return {**detail, "status": "failed", "message": f"{v}: C={g} ref={expected[tuple(v)]}"}
    return {**detail, "status": "passed"}


def file_hint(filename):
    """extr_<flattened upstream path>.c_<function>_Final.c -> (path hint, function)."""
    m = re.match(r"extr_(.*)\.c_(\w+?)_Final\.c$", filename)
    if not m:
        return None, None
    return m.group(1) + ".c", m.group(2)


def load_collection():
    """Read the Jotai manifest, verify file hashes. Returns list of dicts."""
    manifest = os.path.join(JOTAI_DIR, "manifest.csv")
    entries = []
    with open(manifest, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            path = os.path.join(JOTAI_DIR, row["file"])
            with open(path, "rb") as fb:
                data = fb.read()
            entries.append({"file": row["file"], "sha256": row["sha256"], "bytes": int(row["bytes"]),
                            "hash_ok": sha256_bytes(data) == row["sha256"], "text": data.decode("utf-8", "replace")})
    return entries


GENERATED_VECTOR_VALUES = (0, 1, -1, 2, 7, -13, 94, 100, 255, -1000, 4096, 65535)


def extraction_vectors(n_params, rng):
    vecs = [[v] * n_params for v in GENERATED_VECTOR_VALUES[:6]]
    while len(vecs) < 12:
        vecs.append([rng.randint(-100000, 100000) for _ in range(n_params)])
    return vecs


def extract_all(rng_factory, use_gcc=True):
    """Yield one record dict per Jotai file (compatible or not)."""
    gcc = find_gcc() if use_gcc else None
    for entry in load_collection():
        path_hint, fn_hint = file_hint(entry["file"])
        rec = {
            "file": entry["file"], "file_sha256": entry["sha256"], "file_hash_verified": entry["hash_ok"],
            "upstream_path_hint": path_hint, "function_hint": fn_hint,
        }
        try:
            region = function_region(entry["text"])
        except Incompatible as exc:
            yield {**rec, "status": "incompatible", "reason": str(exc), "c_function": None}
            continue
        rec["c_function"] = region
        try:
            fname, params, lines = translate_function(region)
        except Incompatible as exc:
            yield {**rec, "status": "incompatible", "reason": str(exc)}
            continue
        source = "\n".join(lines) + "\n"
        rng = rng_factory(entry["file"])
        upstream = upstream_literal_cases(entry["text"], params)
        candidates = [v for _, v in upstream] + extraction_vectors(len(params), rng)
        tests, expected, dropped = [], {}, 0
        for v in candidates:
            r = run_source_detailed(source, v)
            if r.int32_violations:
                dropped += 1
                continue
            if any(t["inputs"] == v for t in tests):
                continue
            tests.append({"inputs": v, "expected_outputs": list(r.outputs), "expected_error": r.error,
                          "source": "jotai-harness" if v in [u for _, u in upstream] else "generated"})
            if r.error is None and len(r.outputs) == 1:
                expected[tuple(v)] = r.outputs[0]
        rec.update({"function": fname, "params": params, "source": source, "tests": tests,
                    "dropped_int32_vectors": dropped, "upstream_cases": upstream})
        if gcc:
            rec["crosscheck"] = gcc_crosscheck(gcc, entry["text"], region, fname, params, upstream,
                                               [t["inputs"] for t in tests], expected)
        else:
            rec["crosscheck"] = {"status": "skipped", "message": "gcc not found on PATH"}
        rec["status"] = "translated"
        yield rec
