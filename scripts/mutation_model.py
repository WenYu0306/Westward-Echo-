"""Mutation test for the GLM/Qwen model-switch.

For each node file, apply targeted mutations to the ChatOpenAI constructor
params (model name, temperature, max_tokens, reasoning_effort, thinking_budget)
and check whether test_dual_model.py catches each mutation.

KILLED = test fails (good), SURVIVED = test still passes (blind spot).

【中文·这个脚本干什么】
专门针对"模型配置"做变异测试。它故意把 config.py 和各节点文件里的
模型名、温度、max_tokens、思考强度等参数改错，然后跑测试看能不能发现。
如果测试连"把 glm-5.3 改成 glm-5.3-flash"这种明显错误都发现不了，
说明测试对模型配置的覆盖是空的，生产环境可能悄悄用错模型。

【中文·什么是变异测试】
故意把代码改坏（比如把正确的模型名换成错的），然后看测试能不能发现。
如果测试连故意改坏的代码都发现不了，说明测试是摆设、根本靠不住。
这是检验"测试有没有牙齿"的手段。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/mutation_model.py
```

【中文·运行前要准备什么】
① 项目源码必须处于干净状态（没有未提交的修改）
② 确保没有其他人在同时编辑项目源码
③ 不需要 API 密钥（测试是纯单元测试，不调大模型）

【中文·⚠️ 会不会花真钱】
**不会**。本脚本只跑单元测试（test_dual_model.py 和
test_write_guards.py），这些测试不调用大模型 API。

【中文·⚠️ 会产生/修改什么文件】
⚠️ **本脚本运行期间会临时修改项目源码文件**（config.py、
src/agent/nodes/*.py）。每个变异体写入后会立即还原，
并有信号处理器防止中途被杀导致源码残留变异。
但如果进程被 SIGKILL（无法捕获的信号）杀死，
可能留下 .mutation_backup 文件和被改坏的源码。
运行前请确保没有其他编辑在进行，运行后请确认源码已还原。
"""
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/wenyudemac/Documents/dev/Westward Echo（西渡）")

# (file, [(description, old, new), ...])
#
# 【中文】变异体清单。每条是 (描述, 原始文本, 替换文本)。
# 按目标文件分组，覆盖以下类别：
# · 模型默认值改错（glm-5.3 → flash、qwen3.8-max → flash 等）
# · 节点路由改错（READ 路由到 WRITE 的模型档位）
# · 生成参数改错（temperature、max_tokens）
# · 账本记录改错（用硬编码模型名代替实际运行的模型）
# · 路由器逻辑改错（provider 派生、BYOK 优先级、effort 查找）
# ⚠️ 这些变异规则是业务数据，一个字都不能改。
MUTATIONS = [
    # ── config.py: 模型默认值 ──
    # 【中文】这组变异把各模型的默认值改成错误的档位，
    # 期望 test_dual_model.py 能发现"默认模型不对"。
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
    #
    # ── read.py: READ 节点 ──
    # 【中文】把 READ 路由到 WRITE 的模型档位、改温度/max_tokens、
    # 把账本记录的模型名硬编码。期望测试能发现节点用了错误的模型。
    ("src/agent/nodes/read.py", [
        ("route READ to WRITE's tier", 'node_llm_kwargs("read"', 'node_llm_kwargs("write"'),
        ("temperature 0.3->0.4", "temperature=0.3", "temperature=0.4"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("ledger books a constant instead of the model that ran",
         'token_ledger.record("read", model,', 'token_ledger.record("read", "qwen3.8-max",'),
    ]),
    # ── write.py: WRITE 节点 ──
    ("src/agent/nodes/write.py", [
        ("route WRITE to FIX's tier",
         'node_llm_kwargs("write", api_key_override', 'node_llm_kwargs("fix", api_key_override'),
        ("route the 1301 fallback to WRITE's tier",
         '"write_fallback", api_key_override', '"write", api_key_override'),
        ("temperature 0.2->0.3", "temperature=0.2", "temperature=0.3"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
    ]),
    # ── readback.py: READBACK 节点 ──
    ("src/agent/nodes/readback.py", [
        ("route READBACK to FIX's tier",
         'node_llm_kwargs("readback"', 'node_llm_kwargs("fix"'),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 4096->2048", "max_tokens=4096", "max_tokens=2048"),
        ("ledger books a constant instead of the model that ran",
         'token_ledger.record("readback", model,',
         'token_ledger.record("readback", "qwen3.8-max",'),
    ]),
    # ── fix.py: FIX 节点 ──
    ("src/agent/nodes/fix.py", [
        ("route FIX to WRITE's tier", 'node_llm_kwargs("fix"', 'node_llm_kwargs("write"'),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 16384->8192", "max_tokens=16384", "max_tokens=8192"),
        ("ledger books a constant instead of the model that ran",
         'token_ledger.record("fix", model,', 'token_ledger.record("fix", "glm-5.3",'),
    ]),
    # ── review_terms.py: REVIEW_TERMS 节点 ──
    ("src/agent/nodes/review_terms.py", [
        ("route REVIEW_TERMS to WRITE's tier",
         'node_llm_kwargs("review_terms")', 'node_llm_kwargs("write")'),
        ("temperature 0.1->0.3", "temperature=0.1", "temperature=0.3"),
        ("max_tokens 4096->2048", "max_tokens=4096", "max_tokens=2048"),
    ]),
    # The router itself: it is now the single source of truth, so every branch in
    # it is load-bearing (provider derivation, effort lookup, BYOK precedence).
    #
    # ── config.py: 路由器逻辑 ──
    # 【中文】这组变异针对 node_provider()、node_llm_kwargs()、
    # _env_overridden() 和 DEFAULT_NODE_MODELS 表。覆盖了：
    # · provider 派生错误（qwen 模型路由到 GLM 端点）
    # · 未知模型静默兜底而非报错
    # · 环境变量覆盖被忽略
    # · thinking_budget / reasoning_effort 缺失或多余
    # · BYOK key 覆盖被丢弃
    # · 端点 URL 指错
    # · 默认路由表改档
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
#
# 【中文】_INFLIGHT 记录当前正在变异的文件和原始内容。
# 信号处理器用它在中途被杀时还原源码。历史上曾因没有这个机制，
# SIGTERM 导致 review_terms.py 丢失了一行关键代码且未被发现。
_INFLIGHT: dict = {"path": None, "original": None}


def _install_restore_handler():
    """安装信号处理器：SIGTERM/SIGINT 时还原源码再退出。

    【中文】如果脚本在变异过程中被 Ctrl+C 或 kill 命令中断，
    这个处理器会把源码还原成原始版本，防止被改坏的代码留在磁盘上。
    SIGKILL 无法捕获，所以还有 backup 文件作为最后防线。
    """
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
    """跑模型配置相关的单元测试。

    【中文】同时跑 test_dual_model.py（验证每个节点用对了模型/端点）
    和 test_write_guards.py（验证账本归因和 1301 兜底路径）。
    只跑第一个会漏掉"账本记了硬编码模型名"这类变异体。
    超时 300 秒。返回 pytest 的退出码（0=全通过，非0=有失败）。
    """
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
    """主函数：逐个变异体写入 → 跑测试 → 还原 → 记录结果。"""
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
                #
                # 【中文】如果原始文本在当前源码里找不到了，
                # 说明代码已经改过、这个变异体过时了。
                # 记为 STALE 并跳过，不计入 killed 或 survived。
                skipped += 1
                print(f"[STALE] {file} :: {desc} (pattern not found — mutation "
                      f"list is out of date)", flush=True)
                continue
            # Backup for the SIGKILL case the handler cannot catch; refuse to
            # start on a leftover from an earlier death.
            #
            # 【中文】写 backup 文件应对 SIGKILL（信号处理器抓不到）。
            # 如果发现上次留下的 backup，拒绝继续——源码可能已被污染。
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
                # 【中文】无论测试成功还是失败，都必须还原源码。
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
