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
    # READ moved to Qwen 3.8 max (Zhipu's 1301 content filter was rejecting
    # real web-novel chapters). Its mutation set follows the provider: the old
    # GLM_FULL_MODEL / GLM_READ_EFFORT patterns no longer exist in read.py, so
    # leaving them here would silently SKIP and report a fake "0 survived".
    ("src/agent/nodes/read.py", [
        ("model QWEN->GLM (wrong provider)", "model=QWEN_MODEL", "model=GLM_MODEL"),
        ("temperature 0.3->0.4", "temperature=0.3", "temperature=0.4"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("drop thinking_budget", 'extra_body={"thinking_budget": QWEN_THINKING_BUDGET},\n', ""),
        ("thinking_budget 2048->1024", "QWEN_THINKING_BUDGET", "1024"),
        ("add reasoning_effort to Qwen (illegal combo)",
         "        extra_body={\"thinking_budget\": QWEN_THINKING_BUDGET},\n",
         "        extra_body={\"thinking_budget\": QWEN_THINKING_BUDGET},\n        reasoning_effort=\"high\",\n"),
    ]),
    ("src/agent/nodes/write.py", [
        ("model GLM->GLM_FULL (wrong tier)", "model=GLM_MODEL", "model=GLM_FULL_MODEL"),
        ("temperature 0.2->0.3", "temperature=0.2", "temperature=0.3"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("drop reasoning_effort", "reasoning_effort=GLM_WRITE_EFFORT,\n", ""),
    ]),
    ("src/agent/nodes/readback.py", [
        ("model QWEN->GLM (wrong provider)", "model=QWEN_MODEL", "model=GLM_MODEL"),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 4096->2048", "max_tokens=4096", "max_tokens=2048"),
        ("drop thinking_budget", 'extra_body={"thinking_budget": QWEN_THINKING_BUDGET},\n', ""),
        ("thinking_budget 2048->1024", "QWEN_THINKING_BUDGET", "1024"),
    ]),
    ("src/agent/nodes/fix.py", [
        ("model GLM_FULL->GLM (wrong tier)", "model=GLM_FULL_MODEL", "model=GLM_MODEL"),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("drop reasoning_effort", "reasoning_effort=GLM_FIX_EFFORT,\n", ""),
    ]),
    ("src/agent/nodes/review_terms.py", [
        ("model QWEN->GLM (wrong provider)", "model=QWEN_MODEL", "model=GLM_MODEL"),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 4096->2048", "max_tokens=4096", "max_tokens=2048"),
        ("drop thinking_budget", 'extra_body={"thinking_budget": QWEN_THINKING_BUDGET},\n', ""),
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
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_dual_model.py"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=120,
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
