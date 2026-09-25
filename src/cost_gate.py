"""单一来源的试验阶段成本闸门（条款6）。

为什么需要这个模块
------------------
铁律6 要求"实际调 API 前先报具体金额"。在本模块出现之前，这条规矩只有
`scripts/cost/probe_nodes.py::_guard_tier` 一个实现，被复制成 **4 份、3 种写法**：

| 脚本 | 检查的模型 | env 逃生门 | 参数形态 |
|---|---|---|---|
| `scripts/cost/probe_nodes.py:19` | 仅 `QWEN_MODEL` | 有 | 无参，读 `sys.argv[-1]` |
| `scripts/cost/probe_nodes_wide.py:18` | 仅 `QWEN_MODEL` | 有 | 无参，读 `sys.argv[-1]` |
| `scripts/adversarial_test.py:57` | QWEN+GLM+GLM_FULL | 有 | `allow_expensive: bool` |
| `scripts/diag_api_hang.py:43` | QWEN+GLM（**漏 GLM_FULL**） | **无** | 整个 argparse 命名空间 |

这正是 round9 §八 记录的"两套判据各自漂移"同型缺陷：同一件事四个实现，
各自漏掉不同的档位。更严重的是**四份全都在读错的变量**（见下）。

真实缺陷：闸门看的不是实际会跑的模型
------------------------------------
四份闸门都从 `QWEN_MODEL` / `GLM_MODEL` / `GLM_FULL_MODEL` 判断"是不是高价档"。
但节点实际用哪个模型由 `config.NODE_MODELS` 决定，而它可以被逐节点环境变量
`XIDU_NODE_MODEL_<NODE>` 覆盖（`config.py:186-194`）。于是：

* `XIDU_NODE_MODEL_READBACK=qwen3.8-max` 而 `QWEN_MODEL=qwen3.8-flash`
  → 闸门认为"全是 flash，放行"，实际 READBACK 按 ¥36/M 输出价计费。
* 反向同理：降档时闸门可能仍然拒绝，白拦一次。

`config.py:155-158` 已经就 `MODEL_MAP` 记录过这个缺陷类型（"配置声明与真实
行为各走各的"）。本模块以 `NODE_MODELS` 为唯一事实来源，把它堵死。

设计取舍
--------
* **不自己算总价。** 全书成本估算需要按语料标定，那是
  `scripts/cost/estimate_offline.py` 的职责。这里只报**单价**并指向那个脚本，
  避免出现第二套成本模型（又是"两套判据漂移"）。
* **高价档判定沿用项目既有口径**（模型名含 `max` / `prime`），不引入新的
  价格阈值常量——新增魔法常量只会多一个会漂移的判据。
* **不改生产行为。** 本模块只被脚本调用；`src/api`、`src/celery_app` 的正常
  服务路径不经过它，线上翻译不会因为闸门而拒跑。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 经模块取值，不做 `from .config import NODE_MODELS`：`_env_overridden()` 每次
# 返回**新**字典，导入时绑定会在 reload / monkeypatch 之后读到陈旧的那一份——
# 那正是本模块要消灭的"声明与实际路由各走各的"。
from . import config

# 高价档判定口径：与四份旧闸门保持一致（名字里有 max / prime 就是深度档）。
_EXPENSIVE_MARKERS = ("max", "prime")

# 允许跳过闸门的环境变量。四份旧闸门里有三份认它，统一后全部认。
_ALLOW_ENV = "XIDU_ALLOW_EXPENSIVE"


def is_expensive(model: str) -> bool:
    """模型名是否属于高价档。空/未知名字不算高价（由价格表那侧去暴露）。"""
    name = (model or "").strip().lower()
    return any(marker in name for marker in _EXPENSIVE_MARKERS)


def expensive_nodes() -> list[tuple[str, str, float | None]]:
    """实际会被路由到高价档的节点，含其**输出**单价（元/百万 token）。

    返回 ``[(node, model, out_price), ...]``，按节点名排序，便于稳定断言。
    单价查不到时给 ``None`` 而不是猜一个数（`config.MODEL_PRICES_CNY_PER_M`
    是唯一权威价目来源）。

    读的是 `NODE_MODELS` —— 也就是 `node_llm_kwargs()` 真正拿去构造客户端的
    那张表，不是 `QWEN_MODEL` 这类只影响默认值的历史变量。
    """
    out = []
    for node, model in sorted(config.NODE_MODELS.items()):
        if not is_expensive(model):
            continue
        price = config.MODEL_PRICES_CNY_PER_M.get(model)
        out.append((node, model, price[1] if price else None))
    return out


def format_refusal(purpose: str, script_name: str, *, hint: str = "") -> str:
    """渲染拒绝运行的提示。单一来源，四个脚本不再各写一份文案。"""
    lines = expensive_nodes()
    listed = ", ".join(f"{node}={model}" for node, model, _ in lines)
    priced = "\n".join(
        f"       {node:<14} {model:<18} 输出 ¥{price}/M" if price is not None
        else f"       {node:<14} {model:<18} 输出价未在 MODEL_PRICES_CNY_PER_M 中登记"
        for node, model, price in lines
    )
    extra = f"\n{hint.rstrip()}\n" if hint else ""
    return f"""
⛔ 拒绝运行：{listed} 是高价档，而{purpose}

   当前逐节点路由与输出单价（元/百万 token）：
{priced}

   试验阶段请用 flash 档（同样的信息，约 1/10–1/13 的钱）：
       XIDU_NODE_MODEL_READBACK=qwen3.8-flash \\
       XIDU_NODE_MODEL_READ=qwen3.8-flash \\
       XIDU_NODE_MODEL_FIX=glm-5.3-flash \\
           python3 {script_name}

   确实要用高价档，明说（二者择一）：
       python3 {script_name} --allow-expensive
       {_ALLOW_ENV}=1 python3 {script_name}

   要整本书的花费估算（零 API 成本、不调模型）：
       python3 scripts/cost/estimate_offline.py
{extra}"""


def _invoked_script_name() -> str:
    """调用脚本的文件名，用于提示里的示例命令。

    ``sys.argv`` 为空或首项为空串时（嵌入式调用、`python -c` 导入本模块）给
    占位名而不是抛 IndexError——闸门本身绝不能成为新的崩溃点。命名带下划线
    前缀，是为了不和 `format_refusal` 的 `script_name` 形参混淆。
    """
    argv = sys.argv
    if argv and argv[0]:
        return Path(argv[0]).name
    return "script.py"


def guard_tier(*, allow_expensive: bool = False, purpose: str = "本次任务不需要高价档。",
               hint: str = "") -> None:
    """高价档闸门：没有显式放行就拒绝运行。

    ``allow_expensive`` 是脚本自己的 ``--allow-expensive``；环境变量
    ``XIDU_ALLOW_EXPENSIVE=1`` 是等效的全局逃生门。两者都为假且存在高价档
    节点时，打印提示并以退出码 2 结束进程（与四份旧闸门一致，便于脚本化判断）。

    只读 `NODE_MODELS`，因此逐节点升降档都会被正确识别。

    这里不给 `print` 传 `flush=True`：紧随其后的 `sys.exit(2)` 走正常解释器
    退出流程并刷新 stdout，那个参数在本路径上不可观测，只会留下一个任何
    测试都杀不掉的变异点。
    """
    if allow_expensive or os.getenv(_ALLOW_ENV) == "1":
        return
    if not expensive_nodes():
        return
    print(format_refusal(purpose, _invoked_script_name(), hint=hint))
    sys.exit(2)


def describe_tiers() -> str:
    """当前逐节点路由的一行摘要，供脚本在放行后自己打印（"先报金额"的一半）。"""
    parts = []
    for node, model in sorted(config.NODE_MODELS.items()):
        price = config.MODEL_PRICES_CNY_PER_M.get(model)
        mark = "⚠高价" if is_expensive(model) else ""
        out_price = f"¥{price[1]}/M" if price else "未登记"
        parts.append(f"{node}={model}({out_price}){mark}")
    return " | ".join(parts)
