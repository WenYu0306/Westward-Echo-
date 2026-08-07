# Westward Echo 前端设计任务

你先看 `https://westwardecho.com` 现在的页面效果，这是你上次设计的 anchor-v4。在这个基础上继续迭代。

---

## 产品信息

**Westward Echo（西渡）**——AI 文化编译引擎，不是翻译器。

核心原理：中文读者有一个共享文化图像库——看到"鬼节夜敲门"脑内自动补全月光、纸钱灰、村庄屏息。英文读者没有这个库，只看到两个单词。西渡用四个 AI Agent（READ→WRITE→READBACK→FIX）检测这些缺口，用跨文化的感官材料重建场景。

**当前状态**：已上线，内测中。域名 westwardecho.com，Mac Mini 做服务器，Cloudflare Tunnel 穿透。一个人做的，MIT 开源，GitHub: WenYu0306/Westward-Echo-。

**验证数据**：623 万字中文编译成 358 万英文词，两本完整小说（2301章+775章），75+ 次冷读全 PASS，3 份独立审计通过。

---

## 技术约束

- **纯 HTML/CSS/JS**，单文件，不能引入 React/Vue/Tailwind/Bootstrap
- 部署方式：一个 `index.html` 文件，服务器直接读它返回给浏览器
- 上次的 anchor-v4 代码就是最终的交付物，你做的新版本也是同样的方式交付——给我完整的 HTML 文件代码
- 移动端需要响应式（680px断点）

---

## 后端 API

以下是**真实存在的接口**，你的前端必须对接这些：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/translate` | 上传文件开始翻译。FormData: `file`, `content_type`(novel/script/game), `target_lang`(en-US), `api_key`(可选) |
| GET | `/api/translate/{job_id}` | 轮询进度。返回 `{status, current, total, chapter_title}` |
| GET | `/api/jobs` | 获取所有编译记录列表。返回数组 `[{job_id, filename, status, total_chapters, created_at}]` |
| DELETE | `/api/jobs/{job_id}` | 删除一条编译记录 |
| GET | `/api/translation/{job_id}` | 下载译文 markdown |
| GET | `/api/glossary/{job_id}` | 下载术语表 JSON |
| GET | `/api/epub/{job_id}` | 下载 EPUB |

**状态字段值**：`translating`（翻译中）/ `complete`（完成）/ `failed`（失败）

**上传表单字段**：`file`, `content_type`（可选值：`novel`, `script`, `game`）, `target_lang`（默认 `en-US`）, `api_key`（可选）

---

## 当前版本的问题

Anchor-v4 现在是可用的，但我觉得还可以更好。用你的设计判断来决定改什么——可以是视觉层次、空状态、Hero区、信任展示、颜色、字体、交互细节。你是设计师，你来决定这个产品在网页上应该长什么样。

上次你做的纸纹背景、侧边栏布局、四级Agent状态可视化我都保留了。在这个基础上继续推。

---

## 上次代码

你上次交付的 anchor-v4 在：
https://github.com/WenYu0306/Westward-Echo-/blob/main/docs/anchor-v4.html

或者直接打开 westwardecho.com 看实际效果。

---

## 输出要求

给我完整的 HTML 文件代码。我会直接替换服务器上的文件，刷新浏览器就能看到新版本。
