# 西渡 · 第3轮状态（2026-09-23）— 精简交接

> 承接 `HANDOFF_20260923_audit_fix_wip.md`（调查结论全在 §六，仍有效）与
> `HANDOFF_20260923_audit_fix_round2.md`（health/stats 变异已清零）。
> **本文件只记第3轮新增内容。** 工作区改动仍未提交。

## 基线（实测，非声称）
- `python3.11 -m pytest -q` → **1255 passed, 4 deselected**（round2 为 1241）
- 生产数据零污染：ledger / translation_events.db / checkpoints.db / output 四指纹跑测前后全不变
- ⚠️ 必须用 `python3.11`（本机 `python3` 是 3.9）

## 第3轮完成（仍属第1条）

### 1. `job_store` 死方法已删
`update_token_usage` / `get_job_cost` 生产端零调用者（`/jobs/{id}/cost` 已改读台账），
且用废弃 DeepSeek 美元价 → 删除。jobs 表两列保留（删列需迁移，且 `_row_to_dict` 返回
`dict(row)` 无害），已在代码注释标明 inert。
`tests/test_job_store.py`：`TestTokenTracking`(6) → `TestTokenColumnsAreInert`(4)，
锁死「列恒为 0」「complete_job 不写列」「两方法不得复活」。

### 2. `check_source.py` 断头已修（未删，因文件未被 git 跟踪，删除不可恢复）
`from src.encoding` → `from src.text_io`。**已实跑验证**：输出 Ch42「五大仙家」的
胡青山/黄婉儿/灰老六 上下文检索结果，正常。

### 3. `scripts/generate_report.py` — 接线 + 判据重写（第1条"补上调用链"那条）
这是 `docs/UNWIRED_CAPABILITIES.md` 记录的"有脚本能跑、主流程不自动生成"的质量能力。
接线后发现**判据本身是坏的**，已重写：

**(a) 术语一致率是假红。** 原报 19.4%，三层根因：
- 缺席标记混进 `seen_variants` → 同一译法+部分章节缺席 = 2 个"变体" = 判不一致
- `_is_term_consistent` tier2/3 返回 `[variant: <窗口±2词>]` 是**上下文片段不是译法**，
  同译法在不同章节产生不同片段。铁证：`乱坟岗` 被判变体
  `"at the potter's field, the common graves stretched across"` vs
  `"reached the potter's field — the common graves at"` —— 同一译法
- `_extract_candidate_term` 只要求术语**每个词在整章任意位置**出现（不要求相邻）。
  抽查其最高分项，全是噪声：`张婶→Granny Zhang` 匹配到 `deaf granny`（**另一个角色**聋婆婆）；
  `文凭→Old Diplomas` 匹配到普通名词 `a diploma from school`；
  `小石头→Little Stone` 匹配到 `a stone dropped into still water`
→ **结论：任何基于模糊匹配的阈值都是在拟合噪声。** 已改为**只用逐字命中**，
报三态不报单一比率：`VERBATIM_ALL 165 / PARTIAL 747 / NONE 67`（tested 979）。
中间出现过的 62.2%、81.8% 以及上轮 handoff 引用的 83.1% **全部作废**（83.1% 那次采样
自身也带着 `[maybe:]`/`[variant:]` 缺陷）。

**(b) EPUB 是虚假声称。** 报告写"Markdown + EPUB"，但 `find novels/output -name '*.epub'`
= **0**，且 `run_novel.py` 从不构建 EPUB（只有 API 路径 `routes.py:761,807` 按需生成）
→ 改为按磁盘实际存在的格式列出，缺 EPUB 时显式说明。

**(c) 59/59 冷读 PASS 是假绿流入对外文档。** 它被当作报告头牌"编译质量（核心）"展示，
但该记录已在 `HANDOFF_20260921_audit_untrusted.md` §1.2 被判不可信（同源模型自评、
覆盖率 2.6%、抽样章走不同生产路径、0.46% 缺陷率下漏检概率 79%、换采样点 0/6 PASS）
→ 降级为 advisory，标题改「冷读样本（59章/覆盖2.6%）」并声明**不构成全书质量结论**，
新增 `cold_read_is_quality_evidence: false`。

**(d) 评级口径**：`grade` 只由 hard `issues` 决定；`advisories`（术语缺席、冷读警告）
不参与降级 —— 否则报告会因"诚实交代自身抽样局限"而被判 C。

**(e)** 死导入已删（`_is_term_consistent`/`_extract_candidate_term` 判据重写后无调用）。

**新增 `tests/test_generate_report.py`（16 测试）**：锁死逐字判据三态、缺席不等于不一致、
上下文片段不算命中、冷读全 PASS 必须是 advisory 非证据、EPUB 据实声称、
advisory 不降级、JSON 残留必须降级。

### 4. 顺带查实的真实缺陷（属第14条，未处理）
`地府` 全量报告发现 **第 1472 章有 JSON 残留**（模型信封泄漏进交付物）——
这是 hard issue，grade 因此为 B。地府实测：2301 章 / 术语表 1467 / 字数比 3.86x。

## 第1条剩余（3 项）
1. **其余 20+ 变异 target 未在字节偏移 bug 修复后重跑**（最硬的遗留）。
   慢的根因：`tests/conftest.py` 的 `_forbid_live_llm`（autouse）**每个测试无条件
   import 全部 5 个 LLM 节点模块**，连带 langchain ~1.5s；变异测试每个变异体一个
   独立 pytest 进程 → 657 变异体约 2-4 小时。优化方向：守卫改惰性安装（节点模块
   真被 import 时才装）。**约束：不能削弱任何拦截**——这是第6条"裸跑 pytest 恒 ¥0"
   的承重墙，必须保证测试函数中途才 import 节点也能事前拦住。
2. **`name_en` 待用户提供**：5 本书 `name` 全中文，代码回退到 `book_id` ASCII 化并打印
   NOTE，**未臆造英文书名**。需填 `NOVELS[key]["name_en"]`：
   `limitless_horror`(无限恐怖)、`difu`(地府叫我小先生)、`jianke`(间客)、
   `quanzhi_fanwai`(全职高手番外之巅峰荣耀)、`yuannicanlanruyang`(愿你灿烂如阳)。
3. **破坏性操作待确认**：① 已污染历史语料回改（`novels/output/difu_segmented/difu_en.md`
   首行 `# 地府叫我小先生 — English Translation`、`## Chapter 0: Prologue（楔子）`，
   代码已修但存量未回改）；② `output/` 817 文件中约 404 个是测试残留（全部未被 git 跟踪，
   隔离生效后不再新增）。

## 下一步
按用户指定执行顺序，第1条收尾后进 **第5条**（DeepSeek 交接文档逐条验证 + 烂尾修复）。
注意第5条要验证的文档包括本系列三份 handoff —— **其中已被推翻的结论**：
`§9.6「测试套件零 API 调用，跑一次 ¥0」`（实测拦到 11 次 dashscope）、
`上轮 §4.1-3「latency_ms>=0 可批量清计时变异」`（一个都杀不死）、
`上轮引用的 83.1% 术语一致率`（采样自身带缺陷）。
