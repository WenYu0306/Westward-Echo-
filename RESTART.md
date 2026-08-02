# Westward Echo（西渡）— 重启指南

## 这是什么

AI 驱动的中文网文→英文编译引擎。4 个 Agent（READ·WRITE·READBACK·FIX），LangGraph 管道，DeepSeek V4 Flash/Pro 双模。

## 当前状态

- **翻译中**：《地府叫我小先生》2301 章，folk_religion 类型
- **检查点**：`novels/output/difu_segmented/_checkpoint.json`
- **翻译完成**：《无限恐怖》775 章（已验证全 PASS）

## 怎么跑

```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3 -u scripts/run_novel.py difu > novels/output/difu_run.log 2>&1
```

`-u` 是 unbuffered，能实时看进度。检查点自动恢复，挂了重跑会自动续。

## 关键文件

| 文件 | 作用 |
|------|------|
| `novels/output/difu_segmented/difu_en.md` | 翻译输出 |
| `novels/output/difu_segmented/_checkpoint.json` | 检查点（last_idx + glossary snapshot） |
| `novels/output/difu_segmented/_quality.json` | 冷读数据 |
| `novels/output/difu_run.log` | 运行日志 |
| `scripts/run_novel.py difu` | 翻译脚本 |

## 9 个已知 bug

全部已修复。Commit 在本地，还没 push。

## 跑完后要修的小问题

见 memory 文件 `westward_echo_v015_fix_backlog.md`。

## 如果什么都不记得了

读 `README.md` — 英文，架构、验证数据、技术决策全在里面。
