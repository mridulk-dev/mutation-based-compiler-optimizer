"""Group B (randomized): seeded grammar-based generator, version 1.

Programs are NOT built around a particular optimisation. Statements are drawn
from the MiniArith grammar with the probabilities in RANDOM_CONFIG, which is
stored in every manifest record. Features deliberately exercised:

  * negative values (unary minus on literals and sub-expressions),
  * redefinition of inputs and earlier variables (kills, WAR/WAW dependencies),
  * shared sub-expressions drawn from a small per-program pool (natural CSE),
  * `/` and `%` by variables (possible division by zero -> error behaviour) and
    by constants, including occasionally by a literal 0,
  * prints interleaved with assignments (output ordering).

Generated programs are only *candidates*: the dataset builder executes them
and rejects malformed, degenerate, oversized, duplicate or unsafe ones.
"""

import random

RANDOM_VERSION = "1.0"
RANDOM_CONFIG = {
    "n_inputs": [1, 4],
    "n_statements": [3, 14],
    "max_depth": 3,
    "p_leaf": 0.3,
    "p_const_leaf": 0.35,
    "p_negate": 0.1,
    "op_weights": {"+": 3, "-": 3, "*": 3, "/": 1, "%": 1},
    "const_pool": [0, 1, 1, 2, 2, 3, 4, 5, 7, 8, 10, 16, 100],
    "p_divisor_zero_literal": 0.03,
    "p_print": 0.25,
    "p_redefine": 0.3,
    "p_shared_expr": 0.25,
    "shared_pool_size": 3,
    "target_names": ["x", "y", "z", "w", "p", "q", "r", "s"],
}


class RandomProgram:
    def __init__(self, rng, cfg=RANDOM_CONFIG):
        self.rng, self.cfg = rng, cfg
        ops = cfg["op_weights"]
        self.ops = [o for o, w in ops.items() for _ in range(w)]

    def leaf(self, names):
        c = self.cfg
        if self.rng.random() < c["p_const_leaf"]:
            v = self.rng.choice(c["const_pool"])
            return f"-{v}" if v and self.rng.random() < c["p_negate"] else str(v)
        return self.rng.choice(names)

    def expr(self, names, depth, shared):
        c = self.cfg
        if shared and self.rng.random() < c["p_shared_expr"]:
            return self.rng.choice(shared)
        if depth == 0 or self.rng.random() < c["p_leaf"]:
            return self.leaf(names)
        if self.rng.random() < c["p_negate"]:
            return f"-({self.expr(names, depth - 1, shared)})"
        op = self.rng.choice(self.ops)
        left = self.expr(names, depth - 1, shared)
        if op in "/%":
            if self.rng.random() < c["p_divisor_zero_literal"]:
                right = "0"
            elif self.rng.random() < 0.5:
                right = str(self.rng.choice([v for v in c["const_pool"] if v]))
            else:
                right = self.expr(names, depth - 1, shared)
        else:
            right = self.expr(names, depth - 1, shared)
        return f"({left} {op} {right})"

    def build(self):
        c, rng = self.cfg, self.rng
        n_in = rng.randint(*c["n_inputs"])
        inputs = [f"i{k}" for k in range(n_in)]
        names = list(inputs)
        shared = [self.expr(inputs, 1, None) for _ in range(c["shared_pool_size"])]
        shared = [s for s in shared if s.startswith("(")]  # only compound expressions are worth sharing
        lines = [f"input {', '.join(inputs)};"]
        for _ in range(rng.randint(*c["n_statements"])):
            if rng.random() < c["p_print"]:
                lines.append(f"print {self.expr(names, c['max_depth'], shared)};")
                continue
            if rng.random() < c["p_redefine"]:
                target = rng.choice(names)
            else:
                target = rng.choice(c["target_names"])
            lines.append(f"{target} = {self.expr(names, c['max_depth'], shared)};")
            if target not in names:
                names.append(target)
        lines.append(f"print {self.expr(names, 2, shared)};")
        return "\n".join(lines) + "\n"


def generate_program(dataset_seed, pid, cfg=RANDOM_CONFIG):
    rng = random.Random(f"{dataset_seed}:{pid}")
    body = RandomProgram(rng, cfg).build()
    return f"# {pid} [random v{RANDOM_VERSION}]\n" + body
