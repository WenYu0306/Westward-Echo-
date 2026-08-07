# Westward Echo（西渡）— 重启指南

## 这是什么

AI 驱动的中文内容→英文编译引擎。4 个 Agent（READ·WRITE·READBACK·FIX），LangGraph 管道，DeepSeek V4 Flash/Pro 双模。支持网文（novel）与竖屏短剧剧本（script）两种内容类型。

## 当前状态（2026-08-07 更新）

- **已完成**：《无限恐怖》775 章（16+8 次冷读全 PASS，3 份独立审计）
- **已完成**：《地府叫我小先生》2301 章（2026-08-03 完结，59/59 冷读全 PASS，0 失败章节）
- **试点完成**：短剧剧本分支（《父凭子贵》12 集 pilot，3 个采样点全 PASS，输出在 `pilots/output/pei_zong_script/`）
- **进行中**：前端迭代 + 剧本"仅对白编译"模式

## 怎么跑

```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3 -m src.main            # http://localhost:8000
```

终端服务器（当前部署方式）：关掉终端服务就停。launchd 自启动（`com.westwardecho.server`）**目前是坏的**——launchd 环境读不到 ~/Documents（macOS TCC 权限），Python 解释器初始化就崩（见 `/tmp/westward_launchd.log`）。修法：给 CommandLineTools 的 python3 授"完全磁盘访问"，或把项目挪出 Documents。

整本翻译（终端、无沙箱）：

```bash
python3 -u scripts/run_novel.py difu > novels/output/difu_run.log 2>&1
```

检查点自动恢复，挂了重跑会自动续。

## 关键文件

| 文件 | 作用 |
|------|------|
| `novels/output/difu_segmented/difu_en.md` | 地府翻译输出（2301 章完整） |
| `novels/output/difu_segmented/_checkpoint.json` | 检查点（last_idx=2300，已完结） |
| `pilots/output/pei_zong_script/` | 剧本 pilot 输出 + 冷读数据 |
| `scripts/run_script_pilot.py` | 剧本 pilot 入口（⚠️ 输入文件 pilots/pei_zong_script.txt 已在 379c6bc 删除，重跑需先恢复） |
| `docs/anchor-v5.html` | 线上首页（/ 路由读取） |

## 已知问题（2026-08-07 审计）

1. **backpressure 计数泄漏**：`src/api/routes.py` 的 `/translate`、`/translate/multi` 在 `try_accept()` 之后的早退路径（校验失败/坏 key）不 release，坏请求永久占队列槽位
2. **`/translate/multi` 计数不对称**：accept 一次、每语言 release 一次
3. **CI 红**：ruff lint 阶段失败（364 个问题），mypy/pytest 从未跑到；`test_upload_and_poll` 缺 `requires_api_key` marker，lint 修好后在 CI 也会挂
4. **pre-commit 未安装**：`.git/hooks` 为空，README 宣传的钩子从未生效
5. **剧本审计 8 项必修改未落实**（pilots/audit_script_060fbe19.md，已从 repo 删除，可在 git 历史 a02284c 找回）
6. **`game` content type 是空壳**：registry 无分支，静默退回 novel

## 如果什么都不记得了

读 `README.md` — 英文，架构、验证数据、技术决策全在里面。
