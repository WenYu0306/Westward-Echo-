"""Mutation test for the GLM/Qwen model-switch.

For each node file, apply targeted mutations to the ChatOpenAI constructor
params (model name, temperature, max_tokens, reasoning_effort, thinking_budget)
and check whether test_dual_model.py catches each mutation.

KILLED = test fails (good), SURVIVED = test still passes (blind spot).
"""
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
        ("GLM_READ_EFFORT default wrong (high->low)",
         'GLM_READ_EFFORT = os.getenv("GLM_READ_EFFORT", "high")',
         'GLM_READ_EFFORT = os.getenv("GLM_READ_EFFORT", "low")'),
        ("GLM_WRITE_EFFORT default wrong (low->high)",
         'GLM_WRITE_EFFORT = os.getenv("GLM_WRITE_EFFORT", "low")',
         'GLM_WRITE_EFFORT = os.getenv("GLM_WRITE_EFFORT", "high")'),
        ("GLM_FIX_EFFORT default wrong (high->low)",
         'GLM_FIX_EFFORT = os.getenv("GLM_FIX_EFFORT", "high")',
         'GLM_FIX_EFFORT = os.getenv("GLM_FIX_EFFORT", "low")'),
    ]),
    ("src/agent/nodes/read.py", [
        ("model GLM_FULL->GLM (wrong tier)", "model=GLM_FULL_MODEL", "model=GLM_MODEL"),
        ("temperature 0.3->0.4", "temperature=0.3", "temperature=0.4"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("drop reasoning_effort", "reasoning_effort=GLM_READ_EFFORT,\n", ""),
        ("reasoning_effort high->low", "GLM_READ_EFFORT", "GLM_WRITE_EFFORT"),
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
    total = 0
    survived = 0
    for file, mutations in MUTATIONS:
        path = ROOT / file
        original = path.read_text(encoding="utf-8")
        for desc, old, new in mutations:
            if old not in original:
                print(f"SKIP {file} :: {desc} (pattern not found)", flush=True)
                continue
            mutated = original.replace(old, new, 1)
            path.write_text(mutated, encoding="utf-8")
            rc = run_tests()
            path.write_text(original, encoding="utf-8")
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


if __name__ == "__main__":
    main()
