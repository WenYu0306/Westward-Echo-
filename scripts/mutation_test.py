"""Systematic mutation testing for Westward Echo core logic modules.

For each (module, test-file) pair, enumerate AST-level mutants:
  - comparison operator flip (== <-> !=, < <-> >=, > <-> <=, in <-> not in)
  - boolean operator flip (and <-> or)
  - boolean constant flip (True <-> False)
  - numeric constant +1 (threshold drift)
  - return value -> None (detect unasserted returns)

Each mutant is written in place (original is backed up), the module's own
test file(s) are run, and the mutant is scored:
  KILLED   = tests fail (good — the test caught the mutation)
  SURVIVED = tests still pass (bad — a test blind spot)
  TIMEOUT  = the suite never finished; not a kill, reported separately

Usage:
  python scripts/mutation_test.py                 # all modules
  python scripts/mutation_test.py measurements    # one module
  python scripts/mutation_test.py graph_post_process

This is the project's "does the test have teeth" gate. Run it after adding
any logic to confirm the new tests actually catch drift.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (module, [test files])
TARGETS = {
    "cultural_rules": ("src/cultural_rules.py", ["tests/test_cultural_rules.py"]),
    "fidelity": ("src/agent/fidelity.py", ["tests/test_fidelity_check.py", "tests/test_fidelity_gate.py"]),
    "output_guard": ("src/output_guard.py", ["tests/test_output_guard.py"]),
    "chapter_splitter": ("src/chapter_splitter.py", ["tests/test_chapter_splitter.py"]),
    "chapter_slicer": ("src/chapter_slicer.py", ["tests/test_chapter_slicer.py"]),
    "measurements": ("src/measurements.py", ["tests/test_measurements.py"]),
    "idioms": ("src/idioms.py", ["tests/test_idioms.py"]),
    "dialect": ("src/dialect.py", ["tests/test_dialect.py"]),
    "onomatopoeia": ("src/onomatopoeia.py", ["tests/test_onomatopoeia.py"]),
    "sensitive_terms": ("src/sensitive_terms.py", ["tests/test_sensitive_terms.py"]),
    "text_io": ("src/text_io.py", ["tests/test_text_io.py"]),
    # graph.py holds the post-processing gates (fidelity check, fidelity gate,
    # curation gate) — the engineering backstop for compile quality. Only the
    # fast gate tests are used here: the full pipeline tests are too slow to
    # run per mutant.
    "graph_post_process": ("src/agent/graph.py", [
        "tests/test_post_process_gates.py",
        "tests/test_graph_units.py",
        "tests/test_fidelity_gate.py",
        "tests/test_fidelity_check.py",
    ]),
    "cms": ("src/cms.py", ["tests/test_cms_connector.py"]),
    "exact_store": ("src/glossary/exact_store.py", ["tests/test_glossary.py", "tests/test_glossary_accumulation.py"]),
    "semantic_store": ("src/glossary/semantic_store.py", ["tests/test_semantic_store.py"]),
    "style_memo": ("src/style_memo.py", ["tests/test_style_memo.py"]),
    "job_store": ("src/job_store.py", ["tests/test_job_store.py"]),
    "epub_builder": ("src/epub_builder.py", ["tests/test_epub_builder.py"]),
    "backpressure": ("src/backpressure.py", ["tests/test_fault_injection.py"]),
    "circuit_breaker": ("src/circuit_breaker.py", ["tests/test_fault_injection.py"]),
    "error_tracker": ("src/error_tracker.py", ["tests/test_error_tracker.py"]),
    "parse_utils": ("src/agent/parse_utils.py", ["tests/test_v15_pipeline.py"]),
}

COMPARE_FLIP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}


def node_span(source, node):
    lines = source.split("\n")
    start = sum(len(l) + 1 for l in lines[: node.lineno - 1]) + node.col_offset
    end = sum(len(l) + 1 for l in lines[: node.end_lineno - 1]) + node.end_col_offset
    return start, end


def mutants_for(source):
    """Yield (description, mutated_source) for each mutation point."""
    tree = ast.parse(source)
    out = []

    def visit(node):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            old_op = type(node.ops[0])
            if old_op in COMPARE_FLIP:
                s, e = node_span(source, node)
                new = ast.unparse(ast.Compare(
                    left=node.left,
                    ops=[COMPARE_FLIP[old_op]()],
                    comparators=node.comparators,
                ))
                out.append((f"compare {old_op.__name__}->{COMPARE_FLIP[old_op].__name__} at {node.lineno}",
                            source[:s] + new + source[e:]))
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            s, e = node_span(source, node)
            new_op = "or" if isinstance(node.op, ast.And) else "and"
            new = ast.unparse(ast.BoolOp(op=ast.Or() if new_op == "or" else ast.And(),
                                         values=node.values))
            out.append((f"boolop ->{new_op} at {node.lineno}", source[:s] + new + source[e:]))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            s, e = node_span(source, node)
            new = "False" if node.value else "True"
            out.append((f"const bool->{new} at {node.lineno}", source[:s] + new + source[e:]))
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value != 0:
            s, e = node_span(source, node)
            new = repr(node.value + (1 if node.value > 0 else -1))
            out.append((f"const {node.value}->{new} at {node.lineno}", source[:s] + new + source[e:]))
        elif isinstance(node, ast.Return) and node.value is not None and not isinstance(node.value, ast.Constant):
            s, e = node_span(source, node.value)
            seg = ast.unparse(node.value)
            if seg not in ("None", "True", "False"):
                out.append((f"return {seg[:20]}->None at {node.lineno}",
                            source[:s] + "None" + source[e:]))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return out


def run_tests(test_files):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *test_files],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=120,
    )
    return r.returncode


def run_one(key, module_rel, tests):
    module = ROOT / module_rel
    if not module.exists():
        print(f"\n=== {module_rel}: SKIPPED (module no longer exists) ===", flush=True)
        return 0, 0, 0
    missing = [t for t in tests if not (ROOT / t).exists()]
    if missing:
        print(f"\n=== {module_rel}: SKIPPED (test file(s) gone: {', '.join(missing)}) ===",
              flush=True)
        return 0, 0, 0

    original = module.read_text(encoding="utf-8")
    mutants = mutants_for(original)
    print(f"\n=== {module_rel}: {len(mutants)} mutants ===", flush=True)
    survived = 0
    timed_out = 0
    # A long run that is killed mid-mutant would otherwise leave a mutated
    # source file behind. Keep a recoverable copy on disk for the whole run.
    backup = module.with_name(module.name + ".mutation_backup")
    backup.write_text(original, encoding="utf-8")
    try:
        for desc, mutated in mutants:
            module.write_text(mutated, encoding="utf-8")
            try:
                rc = run_tests(tests)
            except subprocess.TimeoutExpired:
                # A timeout is NOT a kill — the suite never rendered a verdict.
                # Counting it as killed would hide a real blind spot.
                rc = None
            finally:
                module.write_text(original, encoding="utf-8")
            if rc is None:
                timed_out += 1
                print(f"  [TIMEOUT]  {desc}", flush=True)
            elif rc == 0:
                survived += 1
                print(f"  [SURVIVED] {desc}", flush=True)
            else:
                print(f"  [killed] {desc}", flush=True)
    finally:
        module.write_text(original, encoding="utf-8")
        backup.unlink(missing_ok=True)
    print(f"  -> {len(mutants) - survived - timed_out}/{len(mutants)} killed"
          f" ({survived} survived, {timed_out} timed out)", flush=True)
    return len(mutants), survived, timed_out


def main():
    keys = sys.argv[1:] or list(TARGETS.keys())
    total = 0
    survived = 0
    timed_out = 0
    for key in keys:
        if key not in TARGETS:
            print(f"unknown module: {key}", flush=True)
            continue
        module_rel, tests = TARGETS[key]
        n, s, t = run_one(key, module_rel, tests)
        total += n
        survived += s
        timed_out += t
    print(f"\n========== SUMMARY ==========", flush=True)
    print(f"total mutants: {total}", flush=True)
    print(f"survived (blind spots): {survived}", flush=True)
    print(f"timed out: {timed_out}", flush=True)
    print(f"killed: {total - survived - timed_out}", flush=True)


if __name__ == "__main__":
    main()
