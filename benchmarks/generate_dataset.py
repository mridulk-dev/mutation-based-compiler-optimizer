"""Reproducible Review-1 benchmark dataset generator.

    python -m benchmarks.generate_dataset            # default seed 2026
    python -m benchmarks.generate_dataset --seed 7

Writes benchmarks/dataset/review1_dataset.json and one .src file per program
under benchmarks/dataset/src/. Each program is built by a seeded,
category-specific template generator; its expected outputs come from running
the test inputs through the reference AST interpreter (execution/reference.py),
never from the optimiser. The same seed always yields byte-identical files.
"""

import argparse
import hashlib
import json
import os
import random

from execution.reference import run_source

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "dataset")
DATASET_PATH = os.path.join(OUT_DIR, "review1_dataset.json")

# category -> number of programs (15 in total)
CATEGORY_PLAN = [
    ("arithmetic", 2),
    ("constant-heavy", 2),
    ("dead-code", 3),
    ("common-subexpression", 3),
    ("strength-reduction", 2),
    ("mixed", 3),
]


# --- expression helpers ----------------------------------------------------------
def atom(rng, names, const_prob=0.25):
    return str(rng.randint(1, 9)) if rng.random() < const_prob else rng.choice(names)


def expr(rng, names, depth=2):
    """Random expression over + - * only (no division by variables, so the
    dataset programs never raise runtime errors)."""
    if depth == 0 or rng.random() < 0.3:
        return atom(rng, names)
    op = rng.choice(["+", "-", "*", "+", "-"])
    return f"({expr(rng, names, depth - 1)} {op} {expr(rng, names, depth - 1)})"


def identity_wrap(rng, e):
    return rng.choice([f"({e} + 0)", f"(0 + {e})", f"({e} - 0)", f"({e} * 1)", f"(1 * {e})", f"({e} / 1)"])


def pick_inputs(rng, lo=2, hi=3):
    return ["a", "b", "c"][: rng.randint(lo, hi)]


# --- category generators: each returns (input names, statement lines) -------------
def gen_arithmetic(rng):
    """Algebraic identities: x+0, 0+x, x-0, x*1, 1*x, x/1 (targets M1, M2)."""
    ins = pick_inputs(rng)
    names, lines = list(ins), []
    for k in range(rng.randint(3, 5)):
        e = expr(rng, names, 2)
        for _ in range(rng.randint(1, 2)):
            e = identity_wrap(rng, e)
        lines.append(f"v{k} = {e};")
        names.append(f"v{k}")
    lines.append(f"print {identity_wrap(rng, names[-1])};")
    lines.append(f"print {names[-2]} - {rng.choice(ins)} * 1;")
    return ins, lines


def gen_constant_heavy(rng):
    """Chains of compile-time constants (targets M7 propagation + M3 folding)."""
    ins = ["n"] if rng.random() < 0.5 else ["n", "m"]
    lines, ks = [f"k0 = {rng.randint(2, 9)};"], ["k0"]
    for j in range(1, rng.randint(3, 5)):
        left = rng.choice(ks + [str(rng.randint(1, 9))])
        right = rng.choice(ks) if rng.random() < 0.5 else str(rng.randint(1, 9))
        lines.append(f"k{j} = {left} {rng.choice(['+', '-', '*'])} {right} {rng.choice(['+', '-'])} {rng.randint(1, 9)};")
        ks.append(f"k{j}")
    lines.append(f"r = {ins[0]} * {rng.choice(ks)} + {rng.choice(ks)};")
    if len(ins) > 1:
        lines.append(f"r = r - {ins[1]} * ({rng.choice(ks)} - {rng.randint(1, 9)});")
    lines.append("print r;")
    lines.append(f"print {ks[-1]} % {rng.randint(3, 9)} + {rng.choice(ks)};")
    return ins, lines


def gen_dead_code(rng):
    """Unused values and overwritten assignments (targets M5)."""
    ins = pick_inputs(rng, 2, 3)
    live, lines = list(ins), []
    for k in range(rng.randint(2, 3)):
        lines.append(f"x{k} = {expr(rng, live, 2)};")
        live.append(f"x{k}")
        if rng.random() < 0.7:  # a dead value nobody reads
            lines.append(f"d{k} = {expr(rng, live, 2)} * {rng.randint(2, 9)};")
    lines.append(f"o = {expr(rng, live, 1)};")  # overwritten before use
    lines.append(f"o = {expr(rng, live, 2)};")
    lines.append(f"junk = o % {rng.randint(2, 9)} + {rng.choice(live)};")
    lines.append("print o;")
    lines.append(f"print {live[-1]};")
    return ins, lines


def gen_cse(rng):
    """Repeated (sometimes commuted) subexpressions (targets M6)."""
    ins = ["a", "b", "c"]
    subs = []
    for _ in range(2):
        p, q = rng.sample(ins, 2)
        op = rng.choice(["+", "*", "-"])
        commuted = f"{q} {op} {p}" if op in "+*" else f"{p} {op} {q}"
        subs.append((f"({p} {op} {q})", f"({commuted})"))
    (s1, s1c), (s2, s2c) = subs
    lines = [
        f"u = {s1} * {rng.choice(ins)} + {s2};",
        f"w = {s1c} - {s2} * {rng.randint(2, 5)};",
        f"z = {s1} + {s2c} + u;",
    ]
    if rng.random() < 0.6:
        lines.append(f"y = {s2} * {s1c} - w;")
        lines.append("print y;")
    lines.append("print u + w;")
    lines.append("print z;")
    return ins, lines


def gen_strength(rng):
    """Multiplication by 2 and other powers of two (targets M4)."""
    ins = pick_inputs(rng, 2, 2)
    names, lines = list(ins), []
    for k in range(rng.randint(3, 5)):
        factor = rng.choice([2, 2, 4, 8, 16, 3])  # 3 = not reducible, control case
        operand = rng.choice(names)
        e = f"{factor} * {operand}" if rng.random() < 0.3 else f"{operand} * {factor}"
        lines.append(f"s{k} = {e} + {rng.choice(names)};")
        names.append(f"s{k}")
    lines.append(f"print {names[-1]} * 2;")
    lines.append(f"print {names[-2]};")
    return ins, lines


def gen_mixed(rng):
    """One feature of every category in a single program."""
    ins = ["a", "b"]
    k = rng.randint(2, 6)
    p, q = rng.sample(ins, 2)
    lines = [
        f"k = {k} * {rng.randint(2, 4)};",
        f"t = {identity_wrap(rng, f'{p} * {q}')};",
        f"u = ({p} + {q}) * k + ({q} + {p});",
        f"dead = u * {rng.randint(2, 9)} - t;",
        f"v = t * {rng.choice([2, 4, 8])} + u;",
        f"w = (({p} + {q}) * 1 + 0) - {rng.choice(['k', str(rng.randint(1, 9))])};",
    ]
    if rng.random() < 0.5:
        lines.append(f"v = v - w * {rng.choice([2, 3])};")
    lines.append("print v;")
    lines.append(f"print w * {rng.choice([2, 4, 5])};")
    return ins, lines


GENERATORS = {
    "arithmetic": gen_arithmetic,
    "constant-heavy": gen_constant_heavy,
    "dead-code": gen_dead_code,
    "common-subexpression": gen_cse,
    "strength-reduction": gen_strength,
    "mixed": gen_mixed,
}


def build_program(seed, pid, category):
    rng = random.Random(f"{seed}:{pid}:{category}")  # str seeds are stable across runs/platforms
    ins, lines = GENERATORS[category](rng)
    source = "\n".join([f"# {pid} [{category}] generated with seed {seed}",
                        f"input {', '.join(ins)};", *lines]) + "\n"
    tests = []
    for _ in range(rng.randint(3, 5)):
        vector = [rng.randint(-20, 20) for _ in ins]
        outputs, failed = run_source(source, vector)
        tests.append({"inputs": vector, "expected_outputs": list(outputs), "expected_error": failed})
    return {"id": pid, "category": category, "inputs": ins, "source": source, "tests": tests}


def generate(seed=2026):
    programs, n = [], 0
    for category, count in CATEGORY_PLAN:
        for _ in range(count):
            n += 1
            programs.append(build_program(seed, f"P{n:02d}", category))
    return {
        "name": "review1-miniarith",
        "seed": seed,
        "generator": "benchmarks/generate_dataset.py",
        "expected_outputs_from": "execution/reference.py (AST interpreter)",
        "categories": dict(CATEGORY_PLAN),
        "programs": programs,
    }


def write(dataset):
    os.makedirs(os.path.join(OUT_DIR, "src"), exist_ok=True)
    text = json.dumps(dataset, indent=2) + "\n"
    with open(DATASET_PATH, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(text)
    for prog in dataset["programs"]:
        path = os.path.join(OUT_DIR, "src", f"{prog['id']}_{prog['category']}.src")
        with open(path, "w", newline="\n", encoding="utf-8") as fh:
            fh.write(prog["source"])
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    dataset = generate(args.seed)
    digest = write(dataset)
    print(f"wrote {len(dataset['programs'])} programs to {DATASET_PATH}")
    print(f"sha256 {digest}")
    for prog in dataset["programs"]:
        errs = sum(t["expected_error"] for t in prog["tests"])
        print(f"  {prog['id']}  {prog['category']:<22} {len(prog['tests'])} tests"
              + (f"  ({errs} expect a runtime error)" if errs else ""))


if __name__ == "__main__":
    main()
