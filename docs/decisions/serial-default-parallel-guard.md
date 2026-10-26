# 单本书编译：固定串行，并行加保险丝

> **状态：已采纳（2026-10-26）。** 默认行为不变（`--batch` 默认 1），
> 新增 `--force-batch` 作为越过保险丝的显式开关。
> 注意与 `concurrency-redesign.md` 区分：那是**多本书之间**的 worker 并行，
> 本文是**一本书内部**的章节并行，两条轴互不相干。

## 决策

单本新书一律串行编译。`scripts/run_novel.py --batch N`（N>1）只适用于
**术语表成熟的老书重跑**；术语表不足 500 条时开并行会被拒绝，除非显式
加 `--force-batch`。

## 为什么不并行（机制级）

串行时的质量链有两条，都依赖"第 N 章看到第 1..N-1 章的结果"：

1. **术语链**：主 agent 的 `_post_process`（`src/agent/graph.py:616`）把本章新术语
   写入主书命名空间 → 下一章的 READ/WRITE 提示词包含全部累积术语。
2. **情节链**：`previous_summary` 来自上一章的真实摘要。

并行路径把两条都剪断，且**术语链的补丁是坏的**：

- 每个 worker 是独立命名空间 `{book_id}_w{index}` 的 agent
  （`scripts/run_novel.py` 的 `translate_batch`）。
- `translate_chapter` 返回的 `new_terms_found` 在 `_finish` 里**被丢弃**——
  收尾只取 `translated_text` / `chapter_title_en` / `chapter_summary` /
  `readback_feedback` 四个字段。
- 下一批 `_flush` 再快照时主表没长过。所以**术语表在整个运行期间冻结在开跑时那一份**。

后果按书的成熟度分档：

| 场景 | 开跑术语表（实测 2026-10-26） | 并行的实际效果 |
|---|---|---|
| 新书 `--batch 8` | 0 条 | 全书在无术语约束下翻译，术语一致性机制完全失效 |
| 地府重跑 | 1460 条 | 基本安全；仅运行中新出现的术语仍会丢 |
| 无限恐怖重跑 | 298 条 | 低于 500 的护栏线，需 `--force-batch` 才放行 |

（`exact_glossary` 里另有 9056 条 `exact_glossary_legacy`，那是 book_id 隔离前的
冻结存档，没有归属、不参与任何 book 的读取，不能当作术语成熟度依据。）

串行下已存在的术语缺陷（「八爷」4 种称呼、咒语 7 种译法，见
`ENGINEERING_RULES.md` 位次 5）在新书并行时会被放大到全书。

## 为什么 A/B 不构成放行依据

并行原型的 MetricX-24 小样本（2 章 × 2 段 × 2 臂 = 8 段）结果为
B−A = +0.203，95% CI [−0.406, +0.812]。

- MetricX 是 reference-free **逐段**质量分，**结构性看不见跨章术语一致性**；
- 原型跑的 400/404 章带着地府的成熟术语快照，本来就落在"安全区"。

所以该实验既不能证明并行变差，也**不能**证明它对任何书安全。速度结论
（5.99×）成立且与该缺陷无关——它是真的，只是代价不在实验的测量范围里。

另外：并行**不省钱**。每章 token 成本与串行相同，省下的只是墙钟时间。

## 保险丝

`scripts/run_novel.py`：

```python
MIN_TERMS_FOR_PARALLEL = 500

def _guard_batch(batch_size, n_terms, force):  # -> 拒绝消息或 None
```

- 检查点在 checkpoint 的术语快照加载**之后**，因此看到的是本次运行真正会用的术语表。
- 拒绝时打印原因并以退出码 1 结束，**在任何 API 调用之前**。
- `--force-batch` 越过检查，用于明确接受术语漂移的场景。

500 这个阈值是量级分界而不是精细标定：新书通常个位数到几十条，地府这类
完成重跑的书 1460 条；取中间是为了不让"播种了几百条"的书被误拦，同时让
两本书之外的任何新书都必须显式 `--force-batch`。代价是无限恐怖（298 条）
重跑时也会被拦——这是有意保守：298 条对 775 章仍然偏薄。

## 没有做的事

- **未修并行术语回流**（worker 的 `new_terms_found` 合并回主表 + 批内冲突仲裁）。
  那是中等规模改动，作用于默认关闭的路径。若将来要把并行用于新书，单独立项。
- **未接 API / Celery**。它们本来就串行，本次不动。
- **未清理 DB 中孤儿 `_w{N}` 命名空间**（原型残留，与本决策无关）。

## 相关

- `docs/decisions/read-tier-downshift.md` — 用降档而不是并发来降本/提速
- `docs/decisions/concurrency-redesign.md` — 多本书之间的 worker 并行（不同轴）
- `tests/test_run_novel.py::TestBatchGuard` — 保险丝的单测
