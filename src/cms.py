"""CMS abstraction layer — reference connectors for pulling novels and pushing translations.

Keep it simple: these are reference implementations for development and demo,
not production integrations. Extend by adding new Connector subclasses.

【中文·这个文件是干什么的】
CMS（Content Management System，内容管理系统）对接层。
简单说就是"从哪儿取小说原文、把翻译成品送到哪儿去"的抽象接口。

【中文·它在整个流程里的位置】
上游：用户通过网页或 API 提交翻译任务时，系统通过这里的连接器获取原文。
下游：翻译完成后，系统通过这里的连接器把成品推送回去。

【中文·设计思路：抽象接口 + 具体实现】
本文件定义了一个抽象基类 CMSConnector（只规定"必须有哪些方法"），
然后提供两个具体实现：
  · FileSystemConnector —— 从本地文件夹读写（开发/演示用）
  · WebhookConnector    —— 通过 HTTP 接口读写（对接外部系统用）
实际使用时由 get_connector() 工厂函数根据配置选择用哪个。
要对接新的 CMS，只需新增一个 CMSConnector 的子类即可。

【中文·⚠️ 安全相关说明】
本文件涉及文件路径拼接和 HTTP 请求，有潜在安全风险。
代码中已做了路径穿越防护（_validate_source_id + resolve 后校验），
但仍有值得注意的地方，已在对应位置用注释标出。
按项目规范，安全问题只在注释中指出，不修改代码。
"""

from __future__ import annotations

import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import httpx

# ═══════════════════════════════════════════════════════════════
# source_id validation (path traversal defense)
# ═══════════════════════════════════════════════════════════════
#
# 【中文·source_id 安全校验】
# source_id 是调用方传入的"小说标识符"，会被拼进文件路径或 URL。
# 如果不加校验，攻击者可以传 "../../etc/passwd" 之类的值来读取任意文件，
# 这叫"路径穿越攻击"（path traversal）。
# 下面的正则和函数就是防这道攻击的第一道防线。

# A source_id must be a plain filename stem: no path separators, no
# traversal sequences, no null bytes. CJK characters, spaces, and book
# title punctuation (《》 etc.) are allowed — novel filenames use them.
#
# 【中文】这条正则匹配"非法字符"：空字节、正斜杠、反斜杠、连续两点。
# 只要 source_id 里出现这些就拒绝——它们都是路径穿越的常用手段。
# 注意允许了中文、空格和书名号等标点，因为小说文件名确实会用到这些。
_BAD_SOURCE_ID = re.compile(r'[\x00/\\]|\.\.')
_MAX_SOURCE_ID_LEN = 200


def _validate_source_id(source_id: str) -> None:
    """Reject source_ids that could escape the connector's scope.

    Raises ValueError on empty/too-long input, path separators, traversal
    sequences, or null bytes. Enforced by every connector's pull_novel.

    【中文·校验 source_id 是否合法】
    三道检查：
      ① 不能为空
      ② 长度不能超过 200（防止超长输入导致异常）
      ③ 不能包含路径分隔符、穿越序列（..）、空字节

    不合法就抛 ValueError，调用方需要捕获处理。
    每个连接器的 pull_novel 方法开头都会调这个函数。
    """
    if not source_id or len(source_id) > _MAX_SOURCE_ID_LEN:
        raise ValueError("Invalid source_id")
    if _BAD_SOURCE_ID.search(source_id):
        raise ValueError("Invalid source_id")

# ═══════════════════════════════════════════════════════════════
# Abstract interface
# ═══════════════════════════════════════════════════════════════
#
# 【中文·抽象基类：定义所有连接器必须实现的接口】
# ABC（Abstract Base Class）是 Python 的"抽象基类"机制，
# 标了 @abstractmethod 的方法，子类必须实现，否则无法实例化。
# 这保证了不管用哪种连接器，调用方都可以放心调 pull_novel / push_translation。


class CMSConnector(ABC):
    """Abstract interface for connecting to content management / copyright systems.

    【中文】CMS 连接器的抽象基类。
    定义了三个方法，其中两个是必须的（pull_novel、push_translation），
    一个是可选的（list_sources，有默认空实现）。
    """

    @abstractmethod
    def pull_novel(self, source_id: str) -> str:
        """Pull a novel's full text from the CMS. Returns the raw txt content.

        Args:
            source_id: An identifier meaningful to the connector (filename, CMS id, URL path).

        【中文·拉取小说原文】
        参数 source_id 的含义取决于具体连接器：
          · FileSystemConnector → 文件名（不含 .txt 后缀）
          · WebhookConnector    → URL 路径中的标识符
        返回纯文本字符串。
        """

    @abstractmethod
    def push_translation(self, job_id: str, platform: str, /) -> dict:
        """Push a completed translation to a publishing platform.

        Args:
            job_id: The Westward Echo job identifier.
            platform: Target platform label (e.g. ``"web"``, ``"kindle"``).

        Returns:
            ``{"url": str, "status": str, "platform_message": str}``

        【中文·推送翻译成品】
        参数中的 / 表示它前面的参数是"仅位置参数"（positional-only），
        调用时不能写成 push_translation(job_id="xxx", ...)，只能按顺序传。
        返回一个字典，包含发布地址、状态和平台消息。
        """

    def list_sources(self) -> list[str]:
        """List available source identifiers. Optional — default returns empty list.

        【中文·列出可用的小说源标识符】
        可选方法，默认返回空列表。FileSystemConnector 覆写了它，
        扫描目录下所有 .txt 文件返回文件名列表。
        """
        return []


# ═══════════════════════════════════════════════════════════════
# File-system connector (dev / demo)
# ═══════════════════════════════════════════════════════════════
#
# 【中文·文件系统连接器】
# 最简单的连接器实现：直接从本地目录读写文件。
# 适用于开发和演示，不适合生产环境。


class FileSystemConnector(CMSConnector):
    """Read novels from a local directory. Useful for development and demo.

    【中文】从本地目录读取小说文件的连接器。
    base_dir 指定小说文件所在的根目录，默认为 ./novels。
    所有文件操作都限制在这个目录内，不允许访问外部路径。
    """

    def __init__(self, base_dir: str = "./novels"):
        # 【中文】resolve() 把相对路径转成绝对路径，消除 .. 和符号链接的影响。
        self.base_dir = Path(base_dir).resolve()

    def pull_novel(self, source_id: str) -> str:
        # 【中文】第一道防线：正则校验 source_id 不含非法字符。
        _validate_source_id(source_id)
        path = self.base_dir / f"{source_id}.txt"
        # Containment check after resolve(): even a well-formed id could
        # land outside base_dir via a symlink placed inside the directory.
        #
        # 【中文】第二道防线：resolve 后再检查路径是否仍在 base_dir 内。
        # 为什么需要两道防线：即使 source_id 本身没有 ../ 之类的内容，
        # 如果 base_dir 里面被人放了一个指向外部的符号链接（symlink），
        # 拼接后的路径仍然可能逃逸出去。resolve() 会把符号链接展开，
        # relative_to() 则验证展开后的路径确实是 base_dir 的子路径。
        try:
            resolved = path.resolve()
            resolved.relative_to(self.base_dir)
        except (OSError, ValueError):
            raise ValueError("Invalid source_id")
        if not path.exists():
            raise FileNotFoundError(f"Novel source not found: {source_id}")
        # 【中文】强制使用 UTF-8 编码读取。
        # 如果小说文件是 GBK 编码的，这里会报错而不是静默乱码。
        return path.read_text(encoding="utf-8")

    def push_translation(self, job_id: str, platform: str, /) -> dict:
        """Save translated output to the local output directory.

        【中文·把翻译成品保存到本地输出目录】
        在 base_dir/output/ 下创建以 job_id 命名的 .md 文件。
        ⚠️ 注意：job_id 没有经过 _validate_source_id 校验！
        虽然 job_id 通常由系统内部生成而非用户直接输入，
        但如果 job_id 被污染（比如包含 ../），理论上也可能路径穿越。
        这是一个潜在的安全隐患，建议后续对 job_id 也做类似的校验。
        """
        output_dir = self.base_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy the Markdown output if it exists
        # 【中文】从全局输出目录复制翻译成品到连接器的输出目录。
        from .config import OUTPUT_DIR

        md_path = OUTPUT_DIR / f"{job_id}_full_novel_en-US.md"
        if md_path.exists():
            dest = output_dir / f"{job_id}.md"
            shutil.copy2(md_path, dest)

        return {
            "url": str(output_dir / f"{job_id}.md"),
            "status": "published",
            "platform_message": f"Saved to {output_dir}",
        }

    def list_sources(self) -> list[str]:
        """List all .txt files in the base directory.

        【中文·扫描 base_dir 下所有 .txt 文件，返回文件名列表（不含后缀）。】
        用于界面展示"当前有哪些小说可翻译"。
        """
        if not self.base_dir.exists():
            return []
        return sorted(
            [p.stem for p in self.base_dir.glob("*.txt") if p.is_file()]
        )


# ═══════════════════════════════════════════════════════════════
# Webhook connector (HTTP-based)
# ═══════════════════════════════════════════════════════════════
#
# 【中文·Webhook 连接器】
# 通过 HTTP 接口与外部 CMS 交互。pull 用 GET 拉取原文，push 用 POST 推送成品。
# 支持 Bearer Token 认证（可选）。


class WebhookConnector(CMSConnector):
    """Push / pull translations via HTTP to a webhook URL.

    【中文】通过 HTTP 请求与远程 CMS 交互的连接器。
    webhook_url 是 CMS 的基础地址，api_key 是可选的认证密钥。
    使用 httpx 库发送 HTTP 请求（类似 requests 但更现代）。
    """

    def __init__(self, webhook_url: str, api_key: str = ""):
        # 【中文】rstrip("/") 去掉 URL 末尾的斜杠，避免拼接时出现双斜杠。
        self.webhook_url = webhook_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialized HTTP client with auth headers.

        【中文·懒加载 HTTP 客户端】
        第一次调用时才创建 httpx.Client 对象，之后复用同一个实例。
        这样避免了不必要的连接开销，也方便测试时 mock。
        如果配了 api_key，会在请求头里带上 Bearer Token 认证信息。
        timeout=30 表示单次请求最多等 30 秒。
        """
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self._client = httpx.Client(headers=headers, timeout=30)
        return self._client

    def pull_novel(self, source_id: str) -> str:
        """Pull novel text from the remote CMS via HTTP GET.

        【中文·通过 HTTP GET 从远程 CMS 拉取小说原文】
        请求地址：{webhook_url}/novels/{source_id}
        ⚠️ source_id 经过了 _validate_source_id 校验，
        但校验只防了路径穿越，没有防 SQL 注入或 URL 注入。
        如果远程 CMS 的实现有漏洞，恶意 source_id 仍可能造成问题。
        不过这是远程服务端的责任，本连接器能做的防护已经做了。
        """
        _validate_source_id(source_id)
        url = f"{self.webhook_url}/novels/{source_id}"
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp.text

    def push_translation(self, job_id: str, platform: str, /) -> dict:
        """Push translated content to the remote CMS via HTTP POST.

        【中文·通过 HTTP POST 把翻译成品推送到远程 CMS】
        请求体是 JSON，包含 job_id、platform 和正文内容。
        成功返回 published 状态，HTTP 错误返回 failed 状态（不抛异常）。
        ⚠️ 同 FileSystemConnector，job_id 未经过 _validate_source_id 校验。
        """
        url = f"{self.webhook_url}/translations/{job_id}"

        # Attach the Markdown body
        # 【中文】从全局输出目录读取翻译成品作为请求体。
        from .config import OUTPUT_DIR

        md_path = OUTPUT_DIR / f"{job_id}_full_novel_en-US.md"
        body = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

        try:
            resp = self.client.post(url, json={
                "job_id": job_id,
                "platform": platform,
                "content": body,
            })
            resp.raise_for_status()
            return {
                "url": url,
                "status": "published",
                # 【中文】只取响应前 200 字符，防止过长的响应塞满日志。
                "platform_message": resp.text[:200],
            }
        except httpx.HTTPError as exc:
            # 【中文】HTTP 错误不抛异常，而是返回 failed 状态。
            # 这样调用方可以根据状态决定重试还是放弃，
            # 不会因为一次网络抖动就中断整个翻译流程。
            return {
                "url": url,
                "status": "failed",
                "platform_message": str(exc),
            }


# ═══════════════════════════════════════════════════════════════
# Factory — picks the right connector from config
# ═══════════════════════════════════════════════════════════════
#
# 【中文·工厂函数：根据配置选择连接器】
# "工厂"是一种设计模式：调用方不需要知道具体用哪个类，
# 只需要调 get_connector()，它会根据环境变量自动选对的那个。


def get_connector() -> CMSConnector:
    """Return the CMS connector configured via environment variables.

    【中文·根据环境变量 CMS_SOURCE_TYPE 返回对应的连接器实例】

    CMS_SOURCE_TYPE 的值决定用哪个连接器：
      "file"    → FileSystemConnector（本地文件，base_dir 由 CMS_FILE_BASE_DIR 指定）
      "webhook" → WebhookConnector（HTTP 接口，URL 和密钥由环境变量指定）
      其他值    → 抛 ValueError

    这些环境变量通常在 .env 文件或部署配置中设置，见 config.py。
    """
    from .config import (
        CMS_FILE_BASE_DIR,
        CMS_SOURCE_TYPE,
        CMS_WEBHOOK_API_KEY,
        CMS_WEBHOOK_URL,
    )

    if CMS_SOURCE_TYPE == "file":
        return FileSystemConnector(base_dir=CMS_FILE_BASE_DIR)

    if CMS_SOURCE_TYPE == "webhook":
        return WebhookConnector(
            webhook_url=CMS_WEBHOOK_URL,
            api_key=CMS_WEBHOOK_API_KEY,
        )

    raise ValueError(
        f"Unknown CMS_SOURCE_TYPE '{CMS_SOURCE_TYPE}'. "
        "Expected 'file' or 'webhook'."
    )
