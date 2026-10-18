# HANDOFF v0.16 — 短剧分支上线 + 安全修复

交接日期：2026-08-08 · 当前 HEAD：`745d3b2` · 版本：v0.16.0

写给下一个接手这个项目的任何人或模型：这份文档自包含，不依赖任何对话上下文。先读这份，再读 README.md，然后跑一遍"接手验证"一节里的命令确认环境。

---

## 一句话现状

网文主线（775 章验证过的四节点读者管线）完全健康；短剧支线已完成管线分支、试点验证和 Web 提交闭环，可以产出；本次还修复了一个中危安全漏洞和一个真实的生产 Bug（EPUB）。

## 本版完成的事（按提交顺序）

### 短剧分支（fcf73bf → 012cda0）
- **registry 分支机制**：`src/agent/prompts/registry.py` 按 `content_type`（novel/script/game）选择四节点提示词。novel 路径恒等（有测试保证字节一致），未知类型回退 novel。
- **短剧四套提示词**：`prompts/script_{read,write,readback,fix}.py`。READ 关注钩子/对白可说性，WRITE 是编剧身份（格式即法律：场景头、角色名、OS、【】面板），READBACK 是"3 秒划走"观众，FIX 最小修改。占位符签名与 novel 版一致（有守卫测试），节点逻辑零改动复用。
- **剧本切分器**：`src/script_splitter.py`，按"第N集"切集、"场景N："识别场景。动作枚举统一复用 `chapter_splitter.ParagraphTag`。
- **试点验证通过**：`pilots/pei_zong_script.txt`（裴总前 3 章改编的 12 集剧本）经 `scripts/run_script_pilot.py` 完整翻译，3 个采样集冷读全 PASS。产出在 `pilots/output/pei_zong_script/`。
- **Web 链路打通**：`routes.py` 的 /translate、/translate/multi、/translate/resume 全链路透传 content_type；Celery 两个任务按类型分流切分；首页 anchor-v4/v5 下拉框已就绪。

### 本次安全与 Bug 修复（31babb4、697ce49、745d3b2）
- **CMS 路径遍历（中危，已修）**：`POST /api/cms/import` 的 source_id 原来直接拼路径，零认证可远程读服务器任意文件。现在 `_validate_source_id`（拒分隔符/`..`/空字节/超长）+ resolve 后目录限定检查（封符号链接逃逸）+ 错误消息不泄露路径。WebhookConnector 同校验。
- **EPUB/Celery（真 Bug，已修）**：Celery 任务合并译文时 `merge_chapters` 不写章节头，而 EPUB 端点的解析器要求 `## Chapter N:` 头——所有走 Celery 的任务下载 EPUB 必 422。新增 `_chapter_md()` 在三个合并点（主任务/resume 循环/resume checkpoint 恢复）补头。同步回退路径本来就有头，未动。
- **cms.py 的 `_has_celery` 漏判 None**（顺手修）：routes.py 有同款防御，cms.py 漏了，Celery 装了但 Redis 挂时合法导入会崩。
- **测试**：新增 `tests/test_cms_security.py`（11 个用例，覆盖遍历变体/符号链接/路径泄露/端点 400）。

## 已验证的（有证据）

| 事项 | 证据 |
|------|------|
| 网文路径零影响 | `test_prompt_registry.py` 恒等测试（novel 模板与原常量字节一致）+ 全量回归 |
| 短剧分支端到端 | 试点 12 集全译完，3/3 冷读 PASS，剧本格式保留（场景头/OS/面板） |
| CMS 遍历封堵 | test_cms_security.py 11 用例全过 |
| EPUB 修复 | `_chapter_md` 输出可被 `_parse_markdown_chapters` 解析（模拟验证） |
| Web script 提交 | test_e2e.py 的 script 用例：按集切分、任务记录带 content_type、mock 管线跑完 |

## 未验证的（诚实盲区，别当成已完成）

1. **EPUB 修复只做了单元级验证**（`_chapter_md` 输出→解析器）。没有跑过"真实 Celery worker 完成一个任务→真实下载 EPUB"的端到端。下次有人部署 Celery 环境时应补这个验证。
2. **短剧长距离稳定性**：试点只有 12 集。风格备忘录在剧本赛道上的累积效果、角色声音跨集一致性——未验证。
3. **style memo 开关 A/B**：从 v0.15 遗留至今，依然没做。
4. **script_mode="dialogue"**（34dc456 引入的对白抽取模式）：管线存在，但试点用的是 full 模式，dialogue 模式没有跑过完整验证。
5. **es-ES/de/fr 的短剧翻译**：LANGUAGE_STYLE_NOTES 覆盖这些语言，但短剧提示词只在 en-US 试点过。
6. **游戏文案分支**：registry 里 game 类型会回退 novel 提示词，纯属占位，未实现。

## 已知问题（按优先级）

1. **既有测试污染（低，但会迷惑人）**：`test_fault_injection.py::test_module_singleton_defaults` 在全量运行时必挂、单独跑必过——是套件内其他测试修改了 backpressure 全局单例的计数没释放。与任何近期改动无关，改之前先确认这个背景，别误判。
2. **ruff format 全项目未对齐**：`.pre-commit-config.yaml` 配了 ruff-format + mypy，但钩子没装（`.git/hooks/pre-commit` 不存在），且全项目文件都不符合 ruff format。别在修复性提交里顺手 format 整个项目，会制造巨大 diff。
3. **DeepSeek V4 Pro 大输出挂起**（v0.15 遗留）：max_tokens>8192 可能永久挂，所有节点已限制在 8192 及以下。根因未查明。
4. **routes.py 下载端点缺 `_safe_job_id`**（低）：get_translation/download_epub/resume/delete_job 未调用校验，只有 get_glossary 调了。job_id 是服务端 UUID 生成的，利用不可行，属纵深防御缺失，可修可不修。

## 接手验证（按顺序跑）

```bash
cd "项目根目录"

# 1. 全量回归（预期：276 passed, 1 failed——那 1 个是上述已知污染）
python3 -m pytest tests/ -q -k "not translate_node"

# 2. 安全测试单跑（预期 11 passed）
python3 -m pytest tests/test_cms_security.py -q

# 3. 短剧切分器对试点素材（预期 12 集）
python3 -c "
from src.script_splitter import split_episodes
text = open('pilots/pei_zong_script.txt', encoding='utf-8').read()
print(len(split_episodes(text)), 'episodes')"

# 4. 试点产出还在
ls pilots/output/pei_zong_script/
```

## 关键文件地图（短剧支线）

```
src/agent/prompts/registry.py        分支选择入口（唯一选择点）
src/agent/prompts/script_*.py        短剧四套提示词
src/script_splitter.py               按集切分 + 场景识别
pilots/pei_zong_script.txt           试点剧本（12 集）
pilots/glossary.json                 试点术语表
scripts/run_script_pilot.py          终端试点脚本（checkpoint + 采样质检）
tests/test_cms_security.py           CMS 安全测试
tests/test_prompt_registry.py        分支恒等性 + 签名守卫测试
```

## 下一步建议（优先级排序）

1. 找一部真实短剧的完整剧本（或扩大自造素材到 60+ 集）跑长距离试点，验证 style memo 在剧本赛道的累积效果。
2. EPUB 端到端验证（部署 Celery 后真实跑一个任务再下载 EPUB）。
3. content_type 传递链路的最后一环：editor_ui 和 review 页还是网文语境（"第 N 章"、功法/年代分类徽章），短剧任务在这些页面措辞会失真，功能可用。
4. game 分支实现（游戏文案的占位符完整性校验是确定性代码，可以先做 output_guard 部分）。

## 踩坑备忘

- 改四节点的提示词选择逻辑时，永远通过 registry，不要直接动 nodes/*.py 的导入。
- 给新内容类型加提示词时，先跑 `tests/test_prompt_registry.py::TestScriptSignature` 同款签名测试，占位符不一致会让节点的 parse 回退静默失效。
- 短剧单集 400-700 字，chapter_slicer（>4500 字触发）天然不干扰，别为剧本改 slicer。
- 提交前 ruff check 过即可，不要跑 ruff format（理由见已知问题 2）。
