"""Mutation test for the GLM/Qwen model-switch.

For each node file, apply targeted mutations to the ChatOpenAI constructor
params (model name, temperature, max_tokens, reasoning_effort, thinking_budget)
and check whether test_dual_model.py catches each mutation.

KILLED = test fails (good), SURVIVED = test still passes (blind spot).
"""
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/wenyudemac/Documents/dev/Westward Echo（西渡）")

# (file, [(description, old, new), ...])
MUTATIONS = [
    ("src/config.py", [
        ("GLM_FULL_MODEL default wrong (glm-5.3->flash)",
         'GLM_FULL_MODEL = os.getenv("GLM_FULL_MODEL", "glm-5.3")',
         'GLM_FULL_MODEL = os.getenv("GLM_FULL_MODEL", "glm-5.3-flash")'),
        ("GLM_MODEL default wrong (flash->full)",
         'GLM_MODEL = os.getenv("GLM_MODEL", "glm-5.3-flash")',
         'GLM_MODEL = os.getenv("GLM_MODEL", "glm-5.3")'),
        ("QWEN_MODEL default wrong (max->flash)",
         'QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.8-max")',
         'QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.8-flash")'),
        ("QWEN_THINKING_BUDGET default wrong (2048->1024)",
         'QWEN_THINKING_BUDGET = int(os.getenv("QWEN_THINKING_BUDGET", "2048"))',
         'QWEN_THINKING_BUDGET = int(os.getenv("QWEN_THINKING_BUDGET", "1024"))'),
        ("GLM_WRITE_EFFORT default wrong (low->high)",
         'GLM_WRITE_EFFORT = os.getenv("GLM_WRITE_EFFORT", "low")',
         'GLM_WRITE_EFFORT = os.getenv("GLM_WRITE_EFFORT", "high")'),
        ("GLM_FIX_EFFORT default wrong (high->low)",
         'GLM_FIX_EFFORT = os.getenv("GLM_FIX_EFFORT", "high")',
         'GLM_FIX_EFFORT = os.getenv("GLM_FIX_EFFORT", "low")'),
    ]),
    # Nodes now take model/provider/reasoning from config.node_llm_kwargs, so the
    # old `model=QWEN_MODEL` patterns are gone. Mutating the *node name* passed to
    # the router is the equivalent defect: it routes the node to another tier.
    # Ledger attribution mutations are covered too — they live in
    # test_write_guards.py, which run_tests() now includes.
    ("src/agent/nodes/read.py", [
        ("route READ to WRITE's tier", 'node_llm_kwargs("read"', 'node_llm_kwargs("write"'),
        ("temperature 0.3->0.4", "temperature=0.3", "temperature=0.4"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("ledger books a constant instead of the model that ran",
         'token_ledger.record("read", model,', 'token_ledger.record("read", "qwen3.8-max",'),
    ]),
    ("src/agent/nodes/write.py", [
        ("route WRITE to FIX's tier",
         'node_llm_kwargs("write", api_key_override', 'node_llm_kwargs("fix", api_key_override'),
        ("route the 1301 fallback to WRITE's tier",
         '"write_fallback", api_key_override', '"write", api_key_override'),
        ("temperature 0.2->0.3", "temperature=0.2", "temperature=0.3"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
    ]),
    ("src/agent/nodes/readback.py", [
        ("route READBACK to FIX's tier",
         'node_llm_kwargs("readback"', 'node_llm_kwargs("fix"'),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 4096->2048", "max_tokens=4096", "max_tokens=2048"),
        ("ledger books a constant instead of the model that ran",
         'token_ledger.record("readback", model,',
         'token_ledger.record("readback", "qwen3.8-max",'),
    ]),
    ("src/agent/nodes/fix.py", [
        ("route FIX to WRITE's tier", 'node_llm_kwargs("fix"', 'node_llm_kwargs("write"'),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("ledger books a constant instead of the model that ran",
         'token_ledger.record("fix", model,', 'token_ledger.record("fix", "glm-5.3",'),
    ]),
    ("src/agent/nodes/review_terms.py", [
        ("route REVIEW_TERMS to WRITE's tier",
         'node_llm_kwargs("review_terms")', 'node_llm_kwargs("write")'),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 4096->2048", "max_tokens=4096", "max_tokens=2048"),
    ]),
    # The router itself: it is now the single source of truth, so every branch in
    # it is load-bearing (provider derivation, effort lookup, BYOK precedence).
    ("src/config.py", [
        ("router sends qwen models to the GLM endpoint",
         '    if name.startswith("qwen"):\n        return "qwen"',
         '    if name.startswith("qwen"):\n        return "glm"'),
        ("router silently defaults unknown models instead of raising",
         '    raise ValueError(\n        f"cannot route model {model!r}',
         '    return "qwen"  # MUTANT\n    raise ValueError(\n        f"cannot route model {model!r}'),
        ("router falls back to a default node instead of raising",
         '    except KeyError:\n        raise ValueError(',
         '    except KeyError:\n        return QWEN_MODEL  # MUTANT\n        raise ValueError('),
        ("router ignores per-node env overrides",
         '        out[node] = os.getenv(f"XIDU_NODE_{prefix}_{node.upper()}", default)',
         '        out[node] = default'),
        ("qwen branch drops thinking_budget",
         '            "extra_body": {"thinking_budget": QWEN_THINKING_BUDGET},\n', ""),
        ("qwen branch illegally adds reasoning_effort",
         '            "extra_body": {"thinking_budget": QWEN_THINKING_BUDGET},\n',
         '            "extra_body": {"thinking_budget": QWEN_THINKING_BUDGET},\n'
         '            "reasoning_effort": "high",\n'),
        ("qwen branch drops the BYOK key override",
         '            "api_key": api_key_override or LLM_API_KEY,',
         '            "api_key": LLM_API_KEY,'),
        ("qwen branch points at the GLM endpoint",
         '            "base_url": LLM_BASE_URL,', '            "base_url": GLM_BASE_URL,'),
        ("glm branch drops the BYOK key override",
         '        "api_key": api_key_override or GLM_API_KEY,',
         '        "api_key": GLM_API_KEY,'),
        ("glm branch ignores the per-node effort table",
         '        "reasoning_effort": NODE_EFFORTS[node],',
         '        "reasoning_effort": "max",'),
        ("glm branch points at the Qwen endpoint",
         '        "base_url": GLM_BASE_URL,\n        "api_key": api_key_override or GLM_API_KEY,',
         '        "base_url": LLM_BASE_URL,\n        "api_key": api_key_override or GLM_API_KEY,'),
        ("routing table re-tiers READBACK by default",
         '    "readback":       QWEN_MODEL,', '    "readback":       GLM_MODEL,'),
        ("routing table re-tiers the 1301 fallback by default",
         '    "write_fallback": QWEN_MODEL,', '    "write_fallback": GLM_MODEL,'),
    ]),
]


# This script rewrites source files in place, so a killed run leaves a mutant
# behind — and unlike mutation_test.py it had no backup at all. That is not
# theoretical: a SIGTERM during a run left
# `extra_body={"thinking_budget": QWEN_THINKING_BUDGET},` deleted from
# src/agent/nodes/review_terms.py. Python tolerated the leftover indentation, so
# the file still compiled and the loss was invisible.
_INFLIGHT: dict = {"path": None, "original": None}


def _install_restore_handler():
    def _restore_and_exit(signum, _frame):
        path, original = _INFLIGHT["path"], _INFLIGHT["original"]
        if path is not None and original is not None:
            try:
                path.write_text(original, encoding="utf-8")
                print(f"\n[signal {signum}] source restored before exit", flush=True)
            except Exception as exc:  # pragma: no cover - best effort
                print(f"\n[signal {signum}] RESTORE FAILED: {exc}", flush=True)
        raise SystemExit(130)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _restore_and_exit)
        except (ValueError, OSError):  # not on the main thread
            pass


def run_tests():
    env = dict(__import__("os").environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Both files are needed: test_dual_model.py pins which model/endpoint each
    # node builds, and test_write_guards.py pins ledger attribution plus the
    # 1301 fallback path. Running only the first would let "book a constant
    # model" mutants survive.
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_dual_model.py", "tests/test_write_guards.py"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=300,
    )
    return r.returncode


def main():
    _install_restore_handler()
    total = 0
    survived = 0
    skipped = 0
    for file, mutations in MUTATIONS:
        path = ROOT / file
        original = path.read_text(encoding="utf-8")
        for desc, old, new in mutations:
            if old not in original:
                # A pattern that no longer exists means this mutation list is
                # stale — the code it was written against has moved. Counting it
                # as anything other than a failure would let a "0 survived"
                # report stand while part of the suite verified nothing.
                skipped += 1
                print(f"[STALE] {file} :: {desc} (pattern not found — mutation "
                      f"list is out of date)", flush=True)
                continue
            # Backup for the SIGKILL case the handler cannot catch; refuse to
            # start on a leftover from an earlier death.
            backup = path.with_name(path.name + ".mutation_backup")
            if backup.exists():
                print(f"[STALE] {file} :: leftover {backup.name} from a killed "
                      f"run — inspect and delete it before re-running", flush=True)
                skipped += 1
                continue
            backup.write_text(original, encoding="utf-8")
            mutated = original.replace(old, new, 1)
            _INFLIGHT["path"] = path
            _INFLIGHT["original"] = original
            path.write_text(mutated, encoding="utf-8")
            try:
                rc = run_tests()
            finally:
                path.write_text(original, encoding="utf-8")
                backup.unlink(missing_ok=True)
                _INFLIGHT["path"] = None
                _INFLIGHT["original"] = None
            total += 1
            if rc == 0:
                survived += 1
                print(f"[SURVIVED] {file} :: {desc}", flush=True)
            else:
                print(f"[killed] {file} :: {desc}", flush=True)

    print(f"\n=== SUMMARY ===", flush=True)
    print(f"total mutants: {total}", flush=True)
    print(f"killed: {total - survived}", flush=True)
    print(f"survived (blind spots): {survived}", flush=True)
    print(f"stale mutations (pattern gone): {skipped}", flush=True)
    if skipped:
        print(f"\nFAIL: {skipped} mutation(s) no longer match the source. Update "
              f"MUTATIONS before trusting this result.", flush=True)


if __name__ == "__main__":
    main()
