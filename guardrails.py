"""安全护栏（§8）：查询审查、注入防护、限流、PII 脱敏、高危操作确认。

原则：只读检索放行；命中违禁主题直接拒绝并给安全替代建议；
疑似提示词注入不解执行、只标记并当普通文本检索；写操作必须 token + confirm。
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field

# 仅覆盖"方法/教程/交易"类真正高危表达；单纯题材词（如"赌博题材小说"）不拦
_BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"(未成年|幼女|儿童|小学生).{0,6}(色情|裸照|性服务|性行为)", "涉及未成年人的性内容"),
    (r"(冰毒|海洛因|制毒|毒品).{0,10}(配方|制作|合成|提纯|教程|方法|步骤|购买渠道)", "毒品制作或交易"),
    (r"(赌博|博彩|六合彩).{0,8}(网站|平台|代理|开户|下注技巧)", "赌博平台推广"),
    (r"(诈骗|杀猪盘).{0,8}(话术|教程|剧本|模板|引流)", "诈骗教程"),
    (r"(自杀|自残).{0,6}(方法|方式|教程|工具)", "自伤方法"),
    (r"(枪支|炸药|爆炸物).{0,8}(制作|配方|教程)", "危险物品制作"),
]

_INJECTION_PATTERNS = [
    r"忽略(以上|上述|之前|前面).{0,10}(指令|要求|规则|设定)",
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instruction|prompt|rule)",
    r"(system|developer)\s*(prompt|message)",
    r"输出(你的)?(系统)?(提示词|prompt|设定)",
    r"你现在(是|扮演).{0,12}(管理员|开发者|root|无限制)",
    r"jailbreak|developer\s*mode|DAN\s*模式",
]

_PII_PATTERNS = [
    (r"1[3-9]\d{9}", "[手机号]"),
    (r"\b\d{17}[\dXx]\b", "[身份证]"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "[邮箱]"),
    (r"\b\d{16,19}\b", "[银行卡]"),
]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class ScreenResult:
    allowed: bool = True
    code: str = "ok"
    message: str = ""
    query: str = ""
    flags: list[str] = field(default_factory=list)
    redacted: str = ""
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "message": self.message,
            "flags": list(self.flags),
            "affected": bool(self.flags),
        }


class RateLimiter:
    """滑动窗口限流（进程内）。多实例部署时换成 Redis。"""

    def __init__(self, per_minute: int = 30):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= self.per_minute:
                retry = max(1, int(60 - (now - bucket[0])))
                return False, retry
            bucket.append(now)
            return True, 0


class Guardrails:
    def __init__(self, max_query_chars: int = 200, rate_limit_per_min: int = 30):
        self.max_query_chars = max_query_chars
        self.rate_limiter = RateLimiter(rate_limit_per_min)

    # ----------------------------------------------------------------- query
    def sanitize(self, query: str) -> str:
        query = _CONTROL_CHARS.sub("", query or "")
        query = re.sub(r"<[^>]{1,80}>", "", query)      # 去 HTML 标签
        return query.strip()[: self.max_query_chars]

    def redact(self, text: str) -> str:
        for pattern, repl in _PII_PATTERNS:
            text = re.sub(pattern, repl, text)
        return text

    def screen(self, query: str, user_id: str, blocked_topics: list[str] | None = None) -> ScreenResult:
        cleaned = self.sanitize(query)
        result = ScreenResult(query=cleaned, redacted=self.redact(cleaned))

        if not cleaned:
            result.allowed = False
            result.code = "empty_query"
            result.message = "请输入主角名称、原文句子或主题后再搜索。"
            return result

        ok, retry = self.rate_limiter.allow(user_id)
        if not ok:
            result.allowed = False
            result.code = "rate_limited"
            result.message = f"请求过于频繁，请 {retry} 秒后再试。"
            return result

        for pattern, reason in _BLOCKED_PATTERNS:
            if re.search(pattern, cleaned, flags=re.I):
                result.allowed = False
                result.code = "blocked_topic"
                result.message = f"该查询涉及{reason}，无法提供检索。"
                result.suggestions = ["换个作品名称或主题试试", "可以按主角名 / 原文句子搜索"]
                result.flags.append("blocked_topic")
                return result

        for topic in blocked_topics or []:
            if topic and topic in cleaned:
                result.allowed = False
                result.code = "user_blocked_topic"
                result.message = "该主题在你的屏蔽列表中。"
                result.flags.append("user_blocked")
                return result

        for pattern in _INJECTION_PATTERNS:
            if re.search(pattern, cleaned, flags=re.I):
                result.flags.append("injection_suspected")
                result.message = "检测到疑似指令内容，已按普通检索文本处理。"
                break
        return result

    # ----------------------------------------------------------------- admin
    def check_admin(self, token: str | None, expected: str, confirm: bool) -> ScreenResult:
        if not token or token != expected:
            return ScreenResult(allowed=False, code="unauthorized", message="需要管理员令牌。")
        if not confirm:
            # 高危写操作必须人工确认（§1.3-7）
            return ScreenResult(allowed=False, code="need_confirmation",
                                message="该操作会改变平台可用状态，请带 confirm=true 再次确认。")
        return ScreenResult()
