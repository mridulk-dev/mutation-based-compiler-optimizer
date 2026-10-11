"""Group A (controlled): parameterised template generators, version 1.

These extend the six Review-1 categories with explicit, recorded knobs:

    size        "small" | "medium" | "large"  - number of template statements
    redundancy  "low" | "high"                - how many optimisation opportunities
                                                are planted per statement

Each program is generated from `random.Random(f"{dataset_seed}:{program_id}")`
(string seeds hash identically on every platform and Python version >= 3.2), so
a program is reproducible from (generator version, dataset seed, id, knobs).
The Review-1 generator (benchmarks/generate_dataset.py) is NOT modified; the 15
Review-1 programs are imported unchanged as a separate controlled subset.
"""

import random

TEMPLATE_VERSION = "1.0"
CATEGORIES = ("arithmetic", "constant-heavy", "dead-code", "common-subexpression",
              "strength-reduction", "mixed")
SIZES = {"small": (3, 4), "medium": (5, 7), "large": (8, 11)}
INPUT_NAMES = ("a", "b", "c", "d")


class Gen:
    def __init__(self, rng, size, redundancy):
        self.rng = rng
        self.size = size
        self.high = redundancy == "high"
        lo, hi = SIZES[size]
        self.n_stmts = rng.randint(lo, hi)

    # --- helpers -------------------------------------------------------------------
    def const(self, lo=1, hi=9, neg=0.15):
        v = self.rng.randint(lo, hi)
        return f"-{v}" if self.rng.random() < neg else str(v)

    def atom(self, names, p_const=0.25):
        return self.const() if self.rng.random() < p_const else self.rng.choice(names)

    def expr(self, names, depth=2, ops=("+", "-", "*", "+", "-")):
        if depth == 0 or self.rng.random() < 0.3:
            return self.atom(names)
        op = self.rng.choice(ops)
        return f"({self.expr(names, depth - 1, ops)} {op} {self.expr(names, depth - 1, ops)})"

    def identity(self, e):
        return self.rng.choice([f"({e} + 0)", f"(0 + {e})", f"({e} - 0)", f"({e} * 1)", f"(1 * {e})",
                                f"({e} / 1)"])

    def inputs(self, lo=2, hi=3):
        return list(INPUT_NAMES[: self.rng.randint(lo, hi)])


# --- categories: each returns (input names, statement lines) ---------------------------
def arithmetic(g):
    ins = g.inputs()
    names, lines = list(ins), []
    for k in range(g.n_stmts):
        e = g.expr(names, 2)
        for _ in range(g.rng.randint(1, 3) if g.high else g.rng.randint(0, 1)):
            e = g.identity(e)
        target = g.rng.choice(names[len(ins):]) if k > 1 and g.rng.random() < 0.2 else f"v{k}"
        lines.append(f"{target} = {e};")  # occasional redefinition of an earlier value
        if target not in names:
            names.append(target)
        if g.rng.random() < 0.25:
            lines.append(f"print {g.identity(target)};")
    lines.append(f"print {g.identity(names[-1])};")
    return ins, lines


def constant_heavy(g):
    ins = ["n"] if g.rng.random() < 0.4 else ["n", "m"]
    ks = ["k0"]
    lines = [f"k0 = {g.const(2, 12)};"]
    ops = ["+", "-", "*", "/", "%"]
    for j in range(1, g.n_stmts):
        left = g.rng.choice(ks + [g.const()])
        right = g.rng.choice(ks) if g.rng.random() < 0.5 else g.const(1, 9, neg=0.3)
        op = g.rng.choice(ops)
        if op in "/%":  # divisor must be a non-zero literal or a constant chain;
            right = str(g.rng.randint(2, 9)) if g.rng.random() < 0.6 else right  # chains are checked below
        extra = f" {g.rng.choice(['+', '-'])} {g.const()}" if g.high else ""
        lines.append(f"k{j} = {left} {op} {right}{extra};")
        ks.append(f"k{j}")
    lines.append(f"r = n * {g.rng.choice(ks)} + {g.rng.choice(ks)};")
    if len(ins) > 1:
        lines.append(f"r = r - m * ({g.rng.choice(ks)} - {g.const()});")
    lines.append("print r;")
    lines.append(f"print {ks[-1]} % {g.rng.randint(3, 9)} + {g.rng.choice(ks)};")
    return ins, lines


def dead_code(g):
    ins = g.inputs(2, 3)
    live, lines = list(ins), []
    for k in range(g.n_stmts):
        lines.append(f"x{k} = {g.expr(live, 2)};")
        live.append(f"x{k}")
        if g.rng.random() < (0.8 if g.high else 0.4):
            dead = f"d{k} = {g.expr(live, 2)} * {g.rng.randint(2, 9)};"
            lines.append(dead)
            if g.high and g.rng.random() < 0.4:  # dead chain: dead value feeding another dead value
                lines.append(f"dd{k} = d{k} + {g.atom(live)};")
        if g.rng.random() < 0.15:  # possibly-trapping dead division: must NOT be removed
            lines.append(f"q{k} = {g.atom(live, 0)} / {g.rng.choice(ins)};")
    lines.append(f"o = {g.expr(live, 1)};")  # overwritten before use
    lines.append(f"o = {g.expr(live, 2)};")
    lines.append(f"junk = o % {g.rng.randint(2, 9)} + {g.rng.choice(live)};")
    lines.append("print o;")
    lines.append(f"print {live[-1]};")
    return ins, lines


def cse(g):
    ins = ["a", "b", "c"]
    n_sub = 3 if g.high else 2
    subs = []
    for _ in range(n_sub):
        p, q = g.rng.sample(ins, 2)
        op = g.rng.choice(["+", "*", "-", "/"]) if g.high else g.rng.choice(["+", "*", "-"])
        commuted = f"{q} {op} {p}" if op in "+*" else f"{p} {op} {q}"
        subs.append((f"({p} {op} {q})", f"({commuted})"))
    lines, names = [], list(ins)
    for k in range(g.n_stmts):
        (s1, s1c), (s2, s2c) = g.rng.sample(subs, 2)
        form = g.rng.randint(0, 3)
        if form == 0:
            e = f"{s1} * {g.atom(names)} + {s2}"
        elif form == 1:
            e = f"{s1c} - {s2} * {g.rng.randint(2, 5)}"
        elif form == 2:
            e = f"{s1} + {s2c} + {g.atom(names)}"
        else:
            e = f"{s2} * {s1c} - {g.atom(names)}"
        lines.append(f"u{k} = {e};")
        names.append(f"u{k}")
        if g.rng.random() < 0.15 and k < g.n_stmts - 1:  # kill availability: redefine an operand
            victim = g.rng.choice(ins)
            lines.append(f"{victim} = {victim} + {g.rng.randint(1, 5)};")
    lines.append(f"print {' + '.join(names[len(ins):][-3:])};")
    lines.append(f"print {names[-1]};")
    return ins, lines


def strength(g):
    ins = g.inputs(2, 3)
    names, lines = list(ins), []
    factors = [2, 2, 4, 8, 16, 32, 3, 6] if g.high else [2, 4, 8, 3, 5, 6, 7]
    for k in range(g.n_stmts):
        f = g.rng.choice(factors)
        operand = g.rng.choice(names)
        e = f"{f} * {operand}" if g.rng.random() < 0.3 else f"{operand} * {f}"
        if g.rng.random() < 0.3:
            e = f"-{e}" if g.rng.random() < 0.5 else f"{e} * {g.rng.choice([2, 4])}"
        lines.append(f"s{k} = {e} + {g.rng.choice(names)};")
        names.append(f"s{k}")
    lines.append(f"print {names[-1]} * 2;")
    lines.append(f"print {names[-2]};")
    return ins, lines




def mixed(g):
    """Statements drawn from every category, interleaved, sharing variables."""
    ins = ["a", "b"] + (["c"] if g.rng.random() < 0.5 else [])
    p, q = g.rng.sample(ins, 2)
    k = g.rng.randint(2, 6)
    lines = [f"k = {k} * {g.rng.randint(2, 4)};"]
    pool = [
        lambda: f"t = {g.identity(f'{p} * {q}')};",
        lambda: f"u = ({p} + {q}) * k + ({q} + {p});",
        lambda: f"dead = u * {g.rng.randint(2, 9)} - t;",
        lambda: f"v = t * {g.rng.choice([2, 4, 8])} + u;",
        lambda: f"w = (({p} + {q}) * 1 + 0) - {g.rng.choice(['k', str(g.rng.randint(1, 9))])};",
        lambda: f"v = v - w * {g.rng.choice([2, 3])};",
        lambda: f"z = (k - {k}) * {g.rng.choice(ins)} + w;",
        lambda: f"y = ({p} - {q}) * ({p} - {q}) + k % {g.rng.randint(2, 5)};",
    ]
    order = [0, 1, 2, 3, 4]
    extra = list(range(5, len(pool)))
    g.rng.shuffle(extra)
    order += extra[: max(0, g.n_stmts - 5)]
    for i in order:
        lines.append(pool[i]())
    outs = ["v", "w"] + [n for n, i in (("z", 6), ("y", 7)) if i in order]
    for o in outs:
        lines.append(f"print {o} * {g.rng.choice([1, 2, 4, 5])};" if g.high else f"print {o};")
    return ins, lines


GENERATORS = {
    "arithmetic": arithmetic,
    "constant-heavy": constant_heavy,
    "dead-code": dead_code,
    "common-subexpression": cse,
    "strength-reduction": strength,
    "mixed": mixed,
}


def generate_program(dataset_seed, pid, category, size, redundancy):
    rng = random.Random(f"{dataset_seed}:{pid}")
    g = Gen(rng, size, redundancy)
    ins, lines = GENERATORS[category](g)
    header = f"# {pid} [{category}, {size}, {redundancy} redundancy] template v{TEMPLATE_VERSION}"
    return "\n".join([header, f"input {', '.join(ins)};", *lines]) + "\n"
