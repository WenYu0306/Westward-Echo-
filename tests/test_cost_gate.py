"""条款6 · 成本闸门（src/cost_gate.py）的行为测试。

这些用例钉住的是**行为**，不是字段值——round5 §五 记录的假绿模式是
"断言一个从不被读取的字段等于某个常量"，这里全部改成断言闸门的放行/拒绝
决定与提示内容。

关键回归：四份旧闸门都读 `QWEN_MODEL`/`GLM_MODEL`，而节点实际路由由
`config.NODE_MODELS`（可被 `XIDU_NODE_MODEL_<NODE>` 逐节点覆盖）决定。
`test_per_node_upgrade_to_expensive_is_caught` 就是这个盲区的钉子：
只改逐节点覆盖、把 legacy 变量留在 flash 档，旧实现会放行，新实现必须拦。
"""
import pytest

from src import config, cost_gate


@pytest.fixture
def all_flash(monkeypatch):
    """把逐节点路由表整体压到 flash 档。"""
    monkeypatch.setattr(config, "NODE_MODELS", {
        "read": "qwen3.8-flash",
        "write": "glm-5.3-flash",
        "write_fallback": "qwen3.8-flash",
        "readback": "qwen3.8-flash",
        "fix": "glm-5.3-flash",
        "review_terms": "qwen3.8-flash",
    })


@pytest.fixture
def readback_on_max(monkeypatch):
    """只有 READBACK 被逐节点升到高价档，其余全 flash。"""
    monkeypatch.setattr(config, "NODE_MODELS", {
        "read": "qwen3.8-flash",
        "write": "glm-5.3-flash",
        "write_fallback": "qwen3.8-flash",
        "readback": "qwen3.8-max",
        "fix": "glm-5.3-flash",
        "review_terms": "qwen3.8-flash",
    })


class TestIsExpensive:
    """高价档判定口径：名字里有 max / prime。"""

    @pytest.mark.parametrize("model", ["qwen3.8-max", "qwen3.8-max-prime", "glm-5.3-MAX"])
    def test_deep_tiers_are_expensive(self, model):
        assert cost_gate.is_expensive(model) is True

    @pytest.mark.parametrize("model", ["qwen3.8-flash", "glm-5.3", "glm-5.3-flashx", "qwen3.7-flash"])
    def test_flash_and_standard_tiers_are_not(self, model):
        assert cost_gate.is_expensive(model) is False

    def test_empty_and_none_are_not_expensive(self):
        assert cost_gate.is_expensive("") is False
        assert cost_gate.is_expensive(None) is False


class TestExpensiveNodes:
    """`expensive_nodes()` 读的是真实路由表，并带上输出单价。"""

    def test_all_flash_yields_nothing(self, all_flash):
        assert cost_gate.expensive_nodes() == []

    def test_per_node_upgrade_is_reported_with_its_price(self, readback_on_max):
        # 字面值：qwen3.8-max 的输出价是 36.0 元/M（config.MODEL_PRICES_CNY_PER_M）。
        assert cost_gate.expensive_nodes() == [("readback", "qwen3.8-max", 36.0)]

    def test_unpriced_model_reports_none_instead_of_guessing(self, monkeypatch):
        monkeypatch.setattr(config, "NODE_MODELS", {"read": "qwen9.9-max"})
        assert cost_gate.expensive_nodes() == [("read", "qwen9.9-max", None)]

    def test_sorted_by_node_name(self, monkeypatch):
        monkeypatch.setattr(config, "NODE_MODELS", {
            "write": "glm-5.3-max",
            "read": "qwen3.8-max",
            "fix": "glm-5.3-max",
        })
        assert [node for node, _, _ in cost_gate.expensive_nodes()] == ["fix", "read", "write"]


class TestGuardTier:
    """闸门的放行/拒绝决定。"""

    def test_all_flash_passes(self, all_flash):
        # 不抛 SystemExit 即为放行。
        cost_gate.guard_tier()

    def test_expensive_tier_refuses_with_exit_code_2(self, readback_on_max):
        with pytest.raises(SystemExit) as exc:
            cost_gate.guard_tier()
        assert exc.value.code == 2

    def test_allow_expensive_flag_passes(self, readback_on_max):
        cost_gate.guard_tier(allow_expensive=True)

    def test_env_escape_hatch_passes(self, readback_on_max, monkeypatch):
        monkeypatch.setenv("XIDU_ALLOW_EXPENSIVE", "1")
        cost_gate.guard_tier()

    def test_env_escape_hatch_requires_exact_value(self, readback_on_max, monkeypatch):
        # "true"/"yes" 不算放行——旧闸门就是精确比 "1"，统一后不变。
        monkeypatch.setenv("XIDU_ALLOW_EXPENSIVE", "true")
        with pytest.raises(SystemExit) as exc:
            cost_gate.guard_tier()
        assert exc.value.code == 2

    def test_refusal_message_names_the_offending_node_and_price(self, readback_on_max, capsys):
        with pytest.raises(SystemExit):
            cost_gate.guard_tier(purpose="本探针只测延迟。")
        out = capsys.readouterr().out
        assert "readback=qwen3.8-max" in out
        assert "¥36.0/M" in out
        assert "本探针只测延迟。" in out
        # 必须把用户指向零成本估算脚本，而不是自己编一个总价。
        assert "estimate_offline.py" in out

    def test_refusal_message_includes_optional_hint(self, readback_on_max, capsys):
        with pytest.raises(SystemExit):
            cost_gate.guard_tier(hint="只想看提示词体积：--tests 6")
        assert "--tests 6" in capsys.readouterr().out

    def test_unpriced_model_says_so_rather_than_printing_a_number(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(config, "NODE_MODELS", {"read": "qwen9.9-max"})
        with pytest.raises(SystemExit):
            cost_gate.guard_tier()
        out = capsys.readouterr().out
        assert "未在 MODEL_PRICES_CNY_PER_M 中登记" in out
        assert "¥" not in out.split("qwen9.9-max")[1].split("\n")[0]


class TestDescribeTiers:
    """放行后打印的一行摘要（"先报金额"的一半）。"""

    def test_lists_every_routed_node_with_price(self, readback_on_max):
        summary = cost_gate.describe_tiers()
        assert "readback=qwen3.8-max(¥36.0/M)⚠高价" in summary
        assert "read=qwen3.8-flash(¥2.7/M)" in summary
        assert "write=glm-5.3-flash(¥2.8/M)" in summary

    def test_unpriced_node_is_flagged_not_priced(self, monkeypatch):
        monkeypatch.setattr(config, "NODE_MODELS", {"read": "qwen9.9-max"})
        assert cost_gate.describe_tiers() == "read=qwen9.9-max(未登记)⚠高价"


class TestInvokedScriptName:
    """提示里的示例命令用哪个脚本名。闸门自己不能成为崩溃点。"""

    def test_normal_argv_yields_the_basename(self, monkeypatch):
        monkeypatch.setattr(cost_gate.sys, "argv", ["/repo/scripts/run_novel.py", "difu"])
        assert cost_gate._invoked_script_name() == "run_novel.py"

    def test_empty_argv_falls_back(self, monkeypatch):
        # 嵌入式调用 / `python -c` 导入时 argv 可能为空：不得抛 IndexError。
        monkeypatch.setattr(cost_gate.sys, "argv", [])
        assert cost_gate._invoked_script_name() == "script.py"

    def test_empty_first_arg_falls_back(self, monkeypatch):
        # argv[0] 为空串同样没有可用名字。
        monkeypatch.setattr(cost_gate.sys, "argv", ["", "--allow-expensive"])
        assert cost_gate._invoked_script_name() == "script.py"


class TestNoSecondCostModel:
    """防漂移：闸门只认逐节点路由表，不认 legacy 变量，也不自带价目。"""

    def test_legacy_model_vars_do_not_change_the_decision(self, all_flash, monkeypatch):
        """四份旧闸门的盲区：legacy 变量说"贵"、实际路由说"便宜"，必须放行。

        旧实现读 QWEN_MODEL/GLM_MODEL，这里把它们全抬到高价档，而真实路由表
        仍是 flash（all_flash）。闸门必须看实际会跑的东西 → 放行。
        """
        monkeypatch.setattr(config, "QWEN_MODEL", "qwen3.8-max")
        monkeypatch.setattr(config, "GLM_MODEL", "glm-5.3-max")
        monkeypatch.setattr(config, "GLM_FULL_MODEL", "glm-5.3-max")
        cost_gate.guard_tier()
        assert cost_gate.expensive_nodes() == []

    def test_per_node_override_is_the_only_thing_that_matters(
        self, all_flash, monkeypatch
    ):
        """反向盲区：legacy 全 flash、但逐节点升到 max，必须拦下。"""
        monkeypatch.setattr(config, "QWEN_MODEL", "qwen3.8-flash")
        monkeypatch.setattr(config, "GLM_MODEL", "glm-5.3-flash")
        monkeypatch.setattr(config, "GLM_FULL_MODEL", "glm-5.3-flash")
        monkeypatch.setitem(config.NODE_MODELS, "readback", "qwen3.8-max")
        assert cost_gate.expensive_nodes() == [("readback", "qwen3.8-max", 36.0)]
        with pytest.raises(SystemExit) as exc:
            cost_gate.guard_tier()
        assert exc.value.code == 2

    def test_price_table_is_the_single_source(self, readback_on_max, monkeypatch):
        """单价必须查 config 的价目表，改表就改提示——证明没有第二份价目。

        用副本字典整体替换，而不是 setitem 原地改：价目表是生产配置的单一
        权威来源，原地改会漏到后续用例里。
        """
        patched = dict(config.MODEL_PRICES_CNY_PER_M)
        patched["qwen3.8-max"] = (1.5, 9.9)
        monkeypatch.setattr(config, "MODEL_PRICES_CNY_PER_M", patched)
        assert cost_gate.expensive_nodes() == [("readback", "qwen3.8-max", 9.9)]
