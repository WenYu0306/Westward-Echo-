"""Translation Style Memo — a 6-drawer knowledge base accumulating lessons
from each translated chapter.

Each book gets its own directory under data/translation_memory/<book_id>/.
Six markdown files track different categories of accumulated translation
experience.

⚠️ Do not reason about prompt size from file size. This docstring used to say
"At ~50 lines per file, the full memo is ~2500-4000 tokens", which was wrong on
a real book by three orders of magnitude: ``difu_xiao_xiansheng`` holds 31,818
lines in bridges.md alone (54,006 lines across all drawers). Nothing prunes the
files — see ``StyleMemoStore`` for the measurement and the reason pruning is a
non-goal here.

What actually bounds the prompt is **read-side truncation**: ``read_all`` keeps
only the newest ~60 lines per drawer, and ``read_relevant`` (what the agent
prompts use) keeps the newest 5 entries per drawer under ``max_chars``. So the
injected memo stays small and constant regardless of how long the book is.

Drawers:
  characters.md — dialogue registers, names, voice, character traits
  pacing.md    — exposition limits, scene rhythm, information density rules
  bridges.md   — cultural bridge patterns that worked (and didn't)
  prose.md     — sentence/paragraph rhythm, show-vs-tell enforcement
  terms.md     — key terminology decisions with cultural reasoning
  MEMO.md      — index pointing to the 5 content files

Consumers (measured, clause 9): READ and WRITE both render ``{style_memo}``
into their user prompt (``prompts/read.py:227``, ``prompts/write.py:180``).
FIX and READBACK deliberately do not — FIX's mandate is the cold reader's
complaints only (``prompts/fix.py`` rule 1), and READBACK must stay a blind
naive reader. Tests pin all four in ``test_prompt_personas.py``.

【中文·翻译经验本】
一、这个文件是干什么的？
    管理"翻译经验本"——一个分六个抽屉的知识库，
    把每章翻译过程中学到的经验持续积累下来。
    翻到第 2000 章时，前 1999 章的经验都在手上，
    所以全书文风不飘、处理方式一致。

二、六个抽屉分别存什么？
    · characters.md —— 人物口吻：对话风格、名字、性格特征
    · pacing.md     —— 节奏规律：说明文上限、场景节奏、信息密度规则
    · bridges.md    —— 文化桥接套路：哪些类比/解释方式有效（或无效）
    · prose.md      —— 行文韵律：句式段落节奏、"展示而非告知"的执行
    · terms.md      —— 术语决策：关键术语的译法及文化理由
    · MEMO.md       —— 索引文件，指向以上五个内容文件

三、它在整个流程里的位置：
    · update_from_read_analysis()：每章都调，从 READ 的分析结果中提取经验
    · update_from_feedback()：只有抽检章节才调，从冷读者反馈中提取经验
    · record_lesson()：底层写入方法，存 FIX 的修补经验等
    · read_relevant()：取每个抽屉最新条目注入 AI 提示词
      （READ 和 WRITE 用，FIX 和 READBACK 不用）

四、核心设计要点：
    · 只追加不删除：文件只增不减，磁盘上无限增长
    · 读取时截断：注入提示词时只取最新几条，所以提示词大小恒定
    · 按指纹去重：record_lesson 用前 50 字符做指纹，避免重复记录

五、谁读这个经验本？
    · READ 和 WRITE 会把 {style_memo} 注入提示词
    · FIX 和 READBACK 故意不读——FIX 只看冷读者的抱怨，
      READBACK 必须保持"不知道这是翻译"的状态
"""

from pathlib import Path

from .config import DATA_DIR, STYLE_MEMO_ENHANCED


class StyleMemoStore:
    """Manages the 6-drawer translation style memo for one book.

    Thread-safe for single-process use (one TranslationAgent per book).

    ⚠️ Updates are append-only and **nothing prunes the files on disk** — this
    docstring used to claim "periodic pruning of obsolete entries", but the
    module has no pruning code: the only writes are ``_ensure_files`` (creates
    empty drawers) and ``record_lesson`` (appends). Measured consequence on a
    real book: ``difu_xiao_xiansheng`` accumulated 31,818 lines / 3.66 MB in
    bridges.md alone, plus 18,329 in terms.md and 3,851 in pacing.md — 54,006
    lines total, against the ~50-lines-per-file this module's docstring assumed
    (636x the largest drawer).

    What keeps prompt cost bounded is *read-side truncation*, not disk pruning:
    ``read_all`` caps each drawer at the newest ~60 lines and ``read_relevant``
    takes the newest 5 entries per drawer under ``max_chars``. So the files grow
    without limit while every prompt stays constant — harmless for tokens, but
    the on-disk store is unbounded and dedup scans the whole file each append
    (``record_lesson`` does a substring test over the entire drawer).

    Pruning is a deliberate non-goal here: dropping accumulated entries would
    change what real translations see, so it needs an owner's decision rather
    than a drive-by edit. Recorded as a finding, not implemented.

    【中文】翻译经验本管理类，管理一本书的六个抽屉文件。
    ⚠️ 重要提醒：
    · 文件只追加不删减，磁盘上会无限增长
      （实测《地府叫我小先生》的 bridges.md 积累了 31,818 行 / 3.66 MB）
    · 但注入提示词时只取最新几条，所以 token 开销恒定
    · 不做自动裁剪：删除旧条目会改变后续翻译看到的内容，
      需要项目负责人决定，不能随手改
    · 每次 record_lesson 都要扫描整个文件做去重检查，
      文件越大越慢（但目前不是瓶颈）
    """

    def __init__(self, book_id: str):
        # 【中文】初始化经验本。
        # · book_id：书籍标识，每本书有独立目录
        # · root：经验本存放路径 data/translation_memory/<book_id>/
        # · _ensure_files()：确保六个抽屉文件都存在
        self.book_id = book_id
        self.root = Path(DATA_DIR) / "translation_memory" / book_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_files()

    def _ensure_files(self):
        """Create empty drawer files if they don't exist.

        【中文】确保六个抽屉文件都存在。
        如果文件不存在就创建并写入标题头；已存在则不动。
        MEMO.md 是索引文件，包含指向其他五个文件的链接。
        """
        files = {
            "MEMO.md": (
                "# Translation Style Memo — Index\n"
                "- [Character Voices](characters.md) — dialogue registers, names, traits\n"
                "- [Pacing & Density](pacing.md) — exposition limits, scene rhythm rules\n"
                "- [Cultural Bridges](bridges.md) — working analogies and bridge patterns\n"
                "- [Prose Rhythm](prose.md) — sentence and paragraph patterns\n"
                "- [Terminology](terms.md) — key terms with cultural reasoning\n"
            ),
            "characters.md": "# Character Voices\n",
            "pacing.md": "# Information Density & Pacing Rules\n",
            "bridges.md": "# Cultural Bridge Patterns\n",
            "prose.md": "# Prose Rhythm Rules\n",
            "terms.md": "# Terminology Decisions with Cultural Reasoning\n",
        }
        for filename, header in files.items():
            path = self.root / filename
            if not path.exists():
                path.write_text(header, encoding="utf-8")

    # ── Public API ──────────────────────────────────────────────

    def read_all(self) -> str:
        """Return the full memo text for injection into the agent prompt.

        Reads MEMO.md + all linked files, concatenated. A drawer over 70 lines
        is cut to its newest ~60 (the oldest are dropped) to keep total tokens
        in check.

        Prefer ``read_relevant()`` for the main agent prompts — it keeps only
        the newest entries per drawer, so prompt size stays constant regardless
        of book length.

        【中文】读取全部经验本内容（供调试或完整查看用）。
        · 返回：所有抽屉文件拼接的字符串，用 "---" 分隔
        · 超过 70 行的抽屉会被截断到最新 60 行（丢弃最旧的）
        · 正式注入提示词时建议用 read_relevant()，它更精简
        """
        parts = []
        for filename in ["MEMO.md"] + self._content_files():
            path = self.root / filename
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            if len(lines) > 70:
                header = lines[0] if lines[0].startswith("#") else ""
                # lines[-60:] never contains lines[0] here (the branch needs
                # >70 lines, 70 > 60), so re-prepending the header cannot
                # duplicate it. This used to read `lines[-60:] if header else
                # lines[-60:]` — both branches identical, so the condition was
                # unobservable dead code (and invisible to mutation testing,
                # whose operators don't cover ternaries).
                #
                # 【中文】取最后 60 行作为保留内容。
                # 因为进入这个分支的前提是行数 > 70，而 70 > 60，
                # 所以 lines[-60:] 不可能包含第一行标题，
                # 重新拼回标题不会导致重复。
                keep = lines[-60:]
                content = (header + "\n" if header else "") + "\n".join(keep)
            parts.append(content)
        return "\n\n---\n\n".join(parts)

    def read_relevant(
        self,
        *,
        max_chars: int = 5000,
        per_drawer_recent: int = 5,
    ) -> str:
        """Return the most recent memo entries, capped at ``max_chars``.

        Instead of dumping the entire accumulated memo into every prompt, this
        takes only the newest entries from each drawer.

        "Relevant" here means **recency**, nothing else: there is no matching
        against the current chapter. The bet is that the newest entries
        naturally track the active cast, the ongoing cultural patterns and the
        current pacing rhythm, while old entries (from ch50 when we're on
        ch1600) are rarely useful and cost prompt tokens.

        This signature used to accept ``chapter_content`` / ``exact_matches`` —
        added by `bdccc85` (2026-08-04, "scalable Style Memo") and never read by
        the body in any revision since. Real content-based retrieval would have
        to search entries, not slice the tail; until that exists, claiming the
        result is chapter-relevant is the same failure mode as a config value
        nobody reads. Callers still pass the chapter's terms into the prompt
        through ``exact_matches_text`` — see ``_make_state``.

        Keeps prompt size **constant** regardless of how many chapters have
        been translated.

        【中文】读取每个抽屉的最新条目，用于注入 AI 提示词。
        ⚠️ 名字有点误导："relevant"其实是"最新"的意思，
           不是"跟当前章节相关"。它不做内容匹配，只按新旧排序。
           赌的是"最新的经验自然跟当前进度最相关"。
        · 参数 max_chars：总字符上限，默认 5000
        · 参数 per_drawer_recent：每个抽屉取最新几条，默认 5
        · 返回：各抽屉最新条目拼接的字符串
        · 每条条目截断到 250 字符，防止单条过长
        · 如果没有任何条目，退化为 read_all()
        """
        parts: list[str] = []
        budget = max_chars

        for drawer in self._content_files():
            if budget <= 200:
                # 剩余预算太少，不值得再塞内容
                break

            path = self.root / drawer
            if not path.exists():
                continue
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) < 2:
                # 只有标题没有内容，跳过
                continue

            header = lines[0]
            # 去掉空行，每条截断到 250 字符
            entries = [line.strip()[:250] for line in lines[1:] if line.strip()]
            if not entries:
                continue

            # Take the most recent N entries
            # 【中文】取最后 N 条（最新的在文件末尾）
            recent = entries[-per_drawer_recent:]
            body = "\n".join(recent)
            block = f"{header}\n{body}"

            # No per-block truncation here. There used to be
            #     if len(block) > budget: block = block[:budget]
            # and it was provably redundant: when it fired, budget dropped to
            # exactly 0, so the `budget <= 200` check broke the loop on the next
            # iteration and the result was a single block that the final
            # `result[:max_chars]` below then cut anyway. The outer cut is the
            # binding constraint on both paths, so the inner one only ever
            # removed characters the outer one would have removed. Verified by
            # exhaustive enumeration over 5,250 (drawer, entry-count, entry-
            # length, max_chars, per_drawer_recent, content) combinations: zero
            # output differences.
            #
            # `len(block) > budget` -> `<=` survived mutation testing precisely
            # because nothing could observe it. A condition no input can
            # distinguish is not a behaviour worth keeping; it is a permanent
            # non-zero survivor count, which is what trains the next reader to
            # stop believing the number.
            #
            # 【中文】这里曾经有逐块截断逻辑，但被证明是多余的：
            # 外层的 result[:max_chars] 已经是最终约束，
            # 内层截断只会移除外层也会移除的字符。
            # 经 5250 种组合穷举验证，输出完全一致，所以删掉了。
            parts.append(block)
            budget -= len(block)

        if not parts:
            return self.read_all()
        result = "\n\n---\n\n".join(parts)
        if len(result) > max_chars:
            # 最终截断：确保总长度不超过上限
            result = result[:max_chars]
        return result

    def record_lesson(
        self,
        drawer: str,
        lesson: str,
        chapter_number: int = 0,
    ):
        """Append a lesson to one of the 5 content drawers.

        Args:
            drawer: one of 'characters', 'pacing', 'bridges', 'prose', 'terms'
            lesson: a single-line or multi-line lesson entry
            chapter_number: which chapter this lesson came from

        【中文】往指定抽屉追加一条经验记录。
        · 参数 drawer：抽屉名，必须是五个内容抽屉之一
        · 参数 lesson：经验内容（单行或多行文本）
        · 参数 chapter_number：来源章节号，会以 [chN] 标签记在条目前面
        · 去重机制：取 lesson 前 50 个字符做指纹（fingerprint），
          如果抽屉文件里已有相同指纹的条目就跳过不写。
          这避免了同一经验被多章重复记录。
        · ⚠️ 去重是全文件子串匹配，文件越大越慢，
          但目前实测不是性能瓶颈。
        """
        filename = f"{drawer}.md"
        if filename not in self._content_files():
            raise ValueError(f"Unknown drawer: {drawer}")
        path = self.root / filename

        # ── Dedup: skip if this lesson already exists ──
        # Extract the first ~50 chars of the lesson (excluding chapter tag)
        # as a fingerprint.  If a previous entry shares the same fingerprint,
        # this lesson is redundant — it was already learned from an earlier
        # chapter.
        #
        # 【中文】去重：取经验文本前 50 字符转小写作为指纹。
        # 如果抽屉文件里已经包含这个指纹，说明这条经验之前记过了，跳过。
        # 50 字符是一个平衡点：太短容易误判重复，太长则几乎不会命中。
        fingerprint = lesson.strip()[:50].lower()
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if fingerprint in existing.lower():
                return  # Duplicate — skip

        tag = f"[ch{chapter_number}]" if chapter_number else ""
        entry = f"\n{tag} {lesson}".rstrip() + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def update_from_read_analysis(
        self,
        read_analysis: dict,
        chapter_number: int,
    ):
        """Extract lessons from the READ agent's analysis — runs EVERY chapter.

        Unlike ``update_from_feedback`` which requires a cold-read (sample
        chapters only), this method pulls directly from the READ agent's
        structured analysis which is available on every chapter regardless
        of fast/sample mode.

        Deterministic extraction — no LLM needed.

        【中文】从 READ 角色的分析结果中提取经验——每章都会调用。
        · 参数 read_analysis：READ 节点输出的结构化分析字典，包含：
          - terminology_decisions：术语决策列表
          - cultural_gaps：文化缺口列表
          - pacing_notes：节奏笔记
          - image_gaps：画面缺口列表
          - crafted_moments：精心构建的场景（增强模式下）
        · 参数 chapter_number：当前章节号
        · 这个方法不需要调大模型，纯确定性提取
        · 与 update_from_feedback 的区别：这个每章都跑，
          后者只有抽检章节才跑
        """
        ra = read_analysis or {}

        # ── Terminology: capture every new term decision ──────
        # 【中文】从术语决策列表中提取每条术语的译法和理由，存入 terms 抽屉。
        # 格式为 "中文 → 英文 // 文化备注" 或 "中文 → 英文 (推理过程)"。
        for td in ra.get("terminology_decisions", []):
            cn = td.get("term_cn", "")
            en = td.get("proposed_en", "")
            reasoning = td.get("reasoning", "")
            cultural_note = td.get("cultural_note", "")
            if cn and en:
                entry = f"{cn} → {en}"
                if cultural_note:
                    entry += f" // {cultural_note[:120]}"
                elif reasoning:
                    entry += f" ({reasoning[:120]})"
                self.record_lesson("terms", entry, chapter_number)

        if STYLE_MEMO_ENHANCED:
            # ── Characters: route character-name decisions ──────
            # 【中文】增强模式下，把人名类术语决策路由到 characters 抽屉。
            # 判断依据：中文词 2~4 个字且有推理或文化备注 → 大概率是人名。
            # Terminology decisions tagged as character names (2-4 char CN
            # with reasoning or cultural_note) are routed to characters.md
            # so the WRITE agent can maintain consistent character voice.
            for td in ra.get("terminology_decisions", []):
                cn = td.get("term_cn", "")
                reasoning = td.get("reasoning", "")
                cultural_note = td.get("cultural_note", "")
                if cn and 2 <= len(cn) <= 4 and (reasoning or cultural_note):
                    entry = f"{cn} → {td.get('proposed_en', '?')}"
                    if cultural_note:
                        entry += f" // {cultural_note[:120]}"
                    self.record_lesson("characters", entry, chapter_number)

            # ── Characters: crafted moments with character focus ──
            # 【中文】增强模式下，把 READ 标记的"精心构建场景"存入 characters 抽屉。
            # 只保留长度 > 20 的有效条目，截断到 200 字符。
            for m in ra.get("crafted_moments", []):
                if isinstance(m, str) and len(m) > 20:
                    self.record_lesson("characters", m[:200], chapter_number)

        # ── Bridges: every cultural gap flagged ────────────────
        # 【中文】把每个文化缺口及对应的桥接策略存入 bridges 抽屉。
        # 格式为 "[策略] 元素 — 指导"，element 截断 60 字符，guidance 截断 150。
        for cg in ra.get("cultural_gaps", []):
            element = cg.get("element", "")[:60]
            strategy = cg.get("bridge_strategy", "context")
            guidance = cg.get("bridge_guidance", "")[:150]
            if element:
                self.record_lesson(
                    "bridges",
                    f"[{strategy}] {element} — {guidance}",
                    chapter_number,
                )

        # ── Pacing: capture the READ agent's structural notes ──
        # 【中文】把 READ 的节奏笔记存入 pacing 抽屉。
        # 只保留长度 > 10 的有效内容，截断到 300 字符。
        pacing = ra.get("pacing_notes", "")
        if pacing and len(pacing.strip()) > 10:
            self.record_lesson("pacing", pacing.strip()[:300], chapter_number)

        if STYLE_MEMO_ENHANCED and pacing and len(pacing.strip()) > 10:
            # ── Prose: sentence/paragraph rhythm from READ analysis ──
            # 【中文】增强模式下，如果节奏笔记中包含句式/段落/韵律相关关键词，
            # 额外存入 prose 抽屉。关键词列表涵盖中英文。
            rhythm_keywords = ["句", "段", "节奏", "密度", "描写", "叙述",
                               "sentence", "paragraph", "rhythm", "density"]
            if any(kw in pacing.lower() for kw in rhythm_keywords):
                self.record_lesson("prose", pacing.strip()[:300], chapter_number)

        # ── Image gaps: record count + priority breakdown ──────
        # 【中文】记录画面缺口的数量和优先级分布，存入 pacing 抽屉。
        # 这提醒 WRITE 角色需要用感官锚点重建这些场景。
        # 如果没有画面缺口，也记一条提醒：可能太抽象了。
        image_gaps = ra.get("image_gaps", [])
        if image_gaps:
            critical = sum(1 for g in image_gaps if g.get("priority") == "critical")
            high = sum(1 for g in image_gaps if g.get("priority") == "high")
            self.record_lesson(
                "pacing",
                f"{len(image_gaps)} image gaps detected (critical={critical}, high={high}). "
                f"WRITER must rebuild these scenes with sensory_anchors.",
                chapter_number,
            )
        else:
            self.record_lesson(
                "pacing",
                "No image gaps detected — chapter may be too abstract. "
                "Check if cultural concepts were explained rather than shown.",
                chapter_number,
            )

    def update_from_feedback(
        self,
        readback_feedback: dict,
        read_analysis: dict,
        chapter_number: int,
    ):
        """Extract concrete lessons from READBACK cold-reader feedback.

        Runs only on sample chapters (when READBACK is active). Complements
        ``update_from_read_analysis`` which runs every chapter.

        【中文】从冷读者（READBACK）反馈中提取经验——仅抽检章节调用。
        · 参数 readback_feedback：冷读者的结构化反馈字典，包含：
          - engagement_gaps：让人想跳过的段落
          - comprehension_issues：看不懂的地方
          - standout_moments：印象深刻的亮点（增强模式下）
          - overall_impression：总体印象
        · 参数 read_analysis：READ 的分析结果（本方法暂未使用）
        · 参数 chapter_number：当前章节号
        · 与 update_from_read_analysis 互补：那个每章跑，这个抽检才跑
        """
        fb = readback_feedback or {}

        # ── Pacing: if reader was bored or wanted to skip ──────
        # 【中文】如果冷读者觉得某段无聊/想跳过，且问题涉及"说明过多"，
        # 记入 pacing 抽屉，提醒后续章节控制说明文长度。
        for eg in fb.get("engagement_gaps", []):
            passage = eg.get("passage", "")[:80]
            issue = eg.get("issue", "")
            if (
                "exposition" in issue.lower()
                or "info" in issue.lower() or "explain" in issue.lower()
            ):
                self.record_lesson(
                    "pacing",
                    f"Exposition drag: '{passage}' — {issue}. Keep "
                    f"explanatory passages ≤3 paragraphs, broken by action.",
                    chapter_number,
                )

        # ── Comprehension: cultural concepts that confused ─────
        # 【中文】冷读者看不懂的文化概念，记入 bridges 抽屉，
        # 提示后续遇到类似情况试试"一句话类比西方等价物"的策略。
        for ci in fb.get("comprehension_issues", []):
            passage = ci.get("passage", "")[:80]
            issue = ci.get("issue", "")
            self.record_lesson(
                "bridges",
                f"Reader confused by '{passage}': {issue}. "
                f"Test: would a one-sentence analogy to Western equivalent fix this?",
                chapter_number,
            )

        if STYLE_MEMO_ENHANCED:
            # ── Prose: cold reader standout moments (positive signal) ──
            # 【中文】增强模式下，冷读者觉得特别好的段落也值得记录，
            # 作为正面样本存入 prose 抽屉。
            for sm in fb.get("standout_moments", []):
                if isinstance(sm, str) and len(sm) > 20:
                    self.record_lesson(
                        "prose",
                        f"Standout: {sm[:200]}",
                        chapter_number,
                    )

        # ── Prose: translation-ish patterns found ───────────────
        # 【中文】如果冷读者的总体印象中提到"像翻译""读起来像译文"，
        # 记入 prose 抽屉，提醒减少"先展示再解释"的模式。
        if fb.get("overall_impression", ""):
            imp = fb["overall_impression"]
            if "translation" in imp.lower() or "reads like" in imp.lower():
                self.record_lesson(
                    "prose",
                    f"Cold reader detected translation feel: {imp[:200]}. "
                    f"Reduce 'show-then-explain' pattern.",
                    chapter_number,
                )

    # ── Internal ────────────────────────────────────────────────

    @staticmethod
    def _content_files() -> list[str]:
        """【中文】返回五个内容抽屉的文件名列表（不含索引文件 MEMO.md）。"""
        return ["characters.md", "pacing.md", "bridges.md", "prose.md", "terms.md"]
