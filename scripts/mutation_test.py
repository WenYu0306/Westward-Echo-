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
import signal
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
    # test_review_api.py is included because list_namespaces and the
    # confirm_term/reject_term row-counts are asserted only there: running the
    # glossary suites alone left 4 survivors on this module. Those row-counts are
    # what let the review API report "no such term" instead of claiming success
    # for an UPDATE that matched zero rows.
    "exact_store": ("src/glossary/exact_store.py", [
        "tests/test_glossary.py",
        "tests/test_glossary_accumulation.py",
        "tests/test_review_api.py",
    ]),
    # The review API had zero coverage before this round, which is how a
    # mount-order bug could leave every endpoint 404ing while all suites passed.
    # A survivor here means human curation can silently stop working again.
    "review_api": ("src/api/review.py", ["tests/test_review_api.py"]),
    "semantic_store": ("src/glossary/semantic_store.py", ["tests/test_semantic_store.py"]),
    "style_memo": ("src/style_memo.py", ["tests/test_style_memo.py"]),
    "job_store": ("src/job_store.py", ["tests/test_job_store.py"]),
    "epub_builder": ("src/epub_builder.py", ["tests/test_epub_builder.py"]),
    "backpressure": ("src/backpressure.py", ["tests/test_fault_injection.py"]),
    "circuit_breaker": ("src/circuit_breaker.py", ["tests/test_fault_injection.py"]),
    "error_tracker": ("src/error_tracker.py", ["tests/test_error_tracker.py"]),
    "parse_utils": ("src/agent/parse_utils.py", ["tests/test_v15_pipeline.py"]),
    # Scene-level completeness check: source-aware mirror for the WRITE prompt's
    # "every scene must have an equivalent" bound. Pure logic, fast tests.
    "completeness": ("src/completeness.py", ["tests/test_completeness.py"]),
    # WRITE's output guards: token-ceiling truncation (_was_truncated) and the
    # Layer 5 prose-rescue/discard boundary. Fast, focused suite.
    # Token accounting: the module that answers "how many tokens did chapter N
    # actually use". Its whole job is arithmetic and counting, so a surviving
    # mutant here means a wrong number could ship silently.
    "token_ledger": ("src/token_ledger.py", ["tests/test_token_ledger.py"]),
    "write_guards": ("src/agent/nodes/write.py", [
        "tests/test_write_guards.py",
        "tests/test_translate_node.py",
        "tests/test_fault_injection.py",
        "tests/test_completeness.py",
    ]),
    # Health checks gate startup and back /api/health plus the dashboard's
    # 5-second poll. A surviving mutant here means either a billable probe can
    # sneak back in, or a broken provider can still report healthy — both are
    # exactly the fake-green failure this audit exists to prevent.
    "health": ("src/health.py", ["tests/test_health.py"]),
    # stats.py feeds the dashboard's live metrics, so a surviving mutant means
    # a wrong number gets displayed as fact.
    "stats": ("src/stats.py", ["tests/test_stats.py"]),
    # Full-corpus deliverable scan (clause 13 gap 4). Pure logic, no LLM, runs
    # on the real 2301-chapter corpus in ~0.4 s. A surviving mutant here means a
    # bad chapter ships as "delivered" — the exact fake-green this replaces:
    # _report.json claims chapters_translated 2301 behind 2.56% cold-read
    # coverage. Its checks are calibrated on measured corpus behaviour, so the
    # calibration constants are load-bearing and must not drift silently.
    "scan_deliverable": ("scripts/scan_deliverable.py", [
        "tests/test_scan_deliverable.py",
    ]),
}

COMPARE_FLIP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}


# ── Proven-equivalent mutants ────────────────────────────────────────────
# Some mutations cannot be killed by ANY test, because no input distinguishes
# the mutant from the original. Reporting those as "survived blind spots" is
# itself a false signal: it leaves a permanent non-zero count that trains the
# next operator to ignore the number, which is exactly how the fake-green
# defects this suite exists to catch stayed hidden for so long.
#
# An entry here must carry a proof. It is a claim that the mutant is
# semantically identical, NOT a place to park a hard-to-test branch — a branch
# that is merely awkward to reach belongs in the test file, not here.
#
# Entries are verified every run: one that matches no generated mutant is
# reported as STALE (usually because the line number moved) rather than being
# silently skipped, so the list cannot quietly rot into hiding real survivors.
EQUIVALENT_MUTANTS = {
    ("src/stats.py", "const 2->3 at 194"): (
        "round(throughput, 2) -> round(throughput, 3). throughput is "
        "recent_5m / 5.0 where recent_5m is an int (sum of 1s over the "
        "completions window), and _max_recent caps it at 200. An integer "
        "divided by 5 has at most one decimal digit (0/.2/.4/.6/.8), so "
        "rounding to 2 or 3 decimals yields the identical decimal and hence "
        "the identical float. Verified by exhaustive enumeration over "
        "k = 0..2000: zero values of k where round(k/5.0, 2) != round(k/5.0, 3)."
    ),
}


def node_span(source, node):
    """Byte offsets of a node, because ast col_offset is a UTF-8 BYTE offset.

    This used to add ``node.col_offset`` to a *character* count of the preceding
    lines. For any line containing multi-byte characters — Chinese comments,
    Chinese string literals, curly quotes, em dashes, all of which this codebase
    is full of — the two units disagree, so the computed offset landed somewhere
    else in the file and the mutation was applied at the WRONG PLACE.

    Consequence, measured: ``const 8->9 at 30`` in src/agent/fidelity.py left
    line 30 completely unchanged (it mutated an unrelated character further
    down), so no test could ever kill it and it was reported as a surviving
    blind spot. A mutation that changes nothing is not evidence about the tests,
    and a mutation that changes the wrong thing is evidence about nothing.

    The returned offsets index the UTF-8 BYTES of the source, so callers must
    slice ``source.encode("utf-8")``, not the str.
    """
    lines = source.split("\n")
    start = sum(len(l.encode("utf-8")) + 1 for l in lines[: node.lineno - 1]) + node.col_offset
    end = sum(len(l.encode("utf-8")) + 1 for l in lines[: node.end_lineno - 1]) + node.end_col_offset
    return start, end


def mutants_for(source):
    """Yield (description, mutated_source) for each mutation point."""
    tree = ast.parse(source)
    data = source.encode("utf-8")
    out = []

    def emit(desc, start, end, replacement):
        """Splice a replacement in at BYTE offsets, then decode back to text."""
        out.append((desc, (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")))

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
                emit(f"compare {old_op.__name__}->{COMPARE_FLIP[old_op].__name__} at {node.lineno}",
                     s, e, new)
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            s, e = node_span(source, node)
            new_op = "or" if isinstance(node.op, ast.And) else "and"
            new = ast.unparse(ast.BoolOp(op=ast.Or() if new_op == "or" else ast.And(),
                                         values=node.values))
            emit(f"boolop ->{new_op} at {node.lineno}", s, e, new)
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            s, e = node_span(source, node)
            emit(f"const bool->{'False' if node.value else 'True'} at {node.lineno}",
                 s, e, "False" if node.value else "True")
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value != 0:
            s, e = node_span(source, node)
            new = repr(node.value + (1 if node.value > 0 else -1))
            emit(f"const {node.value}->{new} at {node.lineno}", s, e, new)
        elif isinstance(node, ast.Return) and node.value is not None and not isinstance(node.value, ast.Constant):
            s, e = node_span(source, node.value)
            seg = ast.unparse(node.value)
            if seg not in ("None", "True", "False"):
                emit(f"return {seg[:20]}->None at {node.lineno}", s, e, "None")
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


# The file currently being mutated, and its pristine text. Kept at module level
# so a signal handler can put the source back: SIGTERM ends a Python process
# without unwinding, so `finally` never runs and a killed run used to leave a
# mutation on disk. That is not theoretical — it left
# `state.get('exact_glossary') and {}` in src/agent/nodes/write.py twice during
# ordinary use. SIGKILL cannot be caught, hence the belt-and-braces refusal
# check in run_one below.
_INFLIGHT: dict = {"module": None, "original": None}


def _install_restore_handler():
    def _restore_and_exit(signum, _frame):
        module, original = _INFLIGHT["module"], _INFLIGHT["original"]
        if module is not None and original is not None:
            try:
                module.write_text(original, encoding="utf-8")
                print(f"\n[signal {signum}] source restored before exit", flush=True)
            except Exception as exc:  # pragma: no cover - best effort
                print(f"\n[signal {signum}] RESTORE FAILED: {exc}", flush=True)
        raise SystemExit(130)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _restore_and_exit)
        except (ValueError, OSError):  # not on the main thread
            pass


def run_one(key, module_rel, tests):
    module = ROOT / module_rel
    if not module.exists():
        print(f"\n=== {module_rel}: SKIPPED (module no longer exists) ===", flush=True)
        return 0, 0, 0, 0, 0, 1
    missing = [t for t in tests if not (ROOT / t).exists()]
    if missing:
        print(f"\n=== {module_rel}: SKIPPED (test file(s) gone: {', '.join(missing)}) ===",
              flush=True)
        return 0, 0, 0, 0, 0, 1

    # A run killed mid-mutant leaves a mutated source file behind: SIGTERM ends
    # the process without unwinding, so the `finally` below never restores it.
    # The backup is meant to be the recoverable copy — but a SUBSEQUENT run
    # overwrote it (backup.write_text below), capturing the already-mutated file
    # as "original" and restoring that at the end. The corruption then became
    # permanent and silent.
    #
    # Not hypothetical: a killed run left `state.get('exact_glossary') and {}`
    # in src/agent/nodes/write.py and the following run baked it in. So refuse
    # to start while a backup is present, and make the operator look. This check
    # must come before the file is read, or the corrupted text becomes "original".
    backup = module.with_name(module.name + ".mutation_backup")
    if backup.exists():
        print(f"\n=== {module_rel}: REFUSING TO RUN ===", flush=True)
        print(f"  A leftover {backup.name} is present, which means a previous "
              f"run was killed mid-mutant.", flush=True)
        print(f"  {module_rel} may still contain a mutation. Diff it against "
              f"{backup.name}, keep the correct version, delete the backup, "
              f"then re-run.", flush=True)
        # Not a pass: the source may still carry a mutation, so the gate
        # rendered no verdict. Returning zeros here is how a corrupted module
        # would get reported as clean.
        return 0, 0, 0, 0, 0, 1

    original = module.read_text(encoding="utf-8")
    mutants = mutants_for(original)
    print(f"\n=== {module_rel}: {len(mutants)} mutants ===", flush=True)

    # Split out the mutants proven unkillable, and check every registry entry
    # for this module actually matched something. An entry that matched nothing
    # means the line moved or the expression was edited — the proof no longer
    # applies to any real mutant, and leaving it registered would let a genuine
    # survivor hide behind a stale excuse.
    registry = {d: p for (m, d), p in EQUIVALENT_MUTANTS.items() if m == module_rel}
    matched = set()
    killable = []
    equivalent = []
    for desc, mutated in mutants:
        if desc in registry:
            matched.add(desc)
            equivalent.append(desc)
        else:
            killable.append((desc, mutated))
    stale = sorted(set(registry) - matched)
    for desc in stale:
        print(f"  [STALE-EQUIVALENCE] registered as unkillable but no longer "
              f"generated: {desc!r}. The proof may no longer apply — re-derive "
              f"it or remove the entry.", flush=True)

    survived = 0
    timed_out = 0

    backup.write_text(original, encoding="utf-8")
    _INFLIGHT["module"] = module
    _INFLIGHT["original"] = original
    try:
        for desc, mutated in killable:
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
        _INFLIGHT["module"] = None
        _INFLIGHT["original"] = None
    killed = len(killable) - survived - timed_out
    print(f"  -> {killed}/{len(killable)} killed"
          f" ({survived} survived, {timed_out} timed out"
          f"{f', {len(equivalent)} proven-equivalent skipped' if equivalent else ''}"
          f"{f', {len(stale)} STALE' if stale else ''})", flush=True)
    # len(killable) is the denominator, not len(mutants): counting an exempted
    # mutant as killed would let the summary report 100% while a real mutant sat
    # untested underneath it.
    return len(killable), len(equivalent), survived, timed_out, len(stale), 0


def main():
    _install_restore_handler()
    keys = sys.argv[1:] or list(TARGETS.keys())
    total = 0
    survived = 0
    timed_out = 0
    stale = 0
    blocked = 0
    equivalent = 0
    for key in keys:
        if key not in TARGETS:
            print(f"unknown module: {key}", flush=True)
            continue
        module_rel, tests = TARGETS[key]
        n, eq, s, t, st, bl = run_one(key, module_rel, tests)
        total += n
        equivalent += eq
        survived += s
        timed_out += t
        stale += st
        blocked += bl
    print(f"\n========== SUMMARY ==========", flush=True)
    print(f"mutants exercised: {total}", flush=True)
    print(f"proven-equivalent (exempt, not exercised): {equivalent}", flush=True)
    print(f"survived (blind spots): {survived}", flush=True)
    print(f"timed out (no verdict): {timed_out}", flush=True)
    print(f"stale equivalence claims: {stale}", flush=True)
    print(f"blocked (no verdict rendered): {blocked}", flush=True)
    print(f"killed: {total - survived - timed_out}", flush=True)

    # A gate that cannot fail is documentation, not a gate. Every rule this
    # project added after the audit is enforced in code for the same reason:
    # "survived must be zero" is worthless if nothing refuses to run red.
    if survived or timed_out or stale or blocked:
        print("\nFAIL: unresolved mutation results above.", flush=True)
        return 1
    print("\nOK: every generated mutant was killed or is registered with a proof.",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
