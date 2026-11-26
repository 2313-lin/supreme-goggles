"""编排层：预算控制、并行召回、熔断联动、降级拓展、结果组装（§2 / §4 / §7）。

一次搜索的完整生命周期都收敛在 `SearchAgent.search()`，便于端到端测试。
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from .adapters import AdapterError, AdapterHub
from .audit import AuditLog
from .channels import layer_of
from .config import Settings
from .corpus import Corpus
from .guardrails import Guardrails
from .memory import MemoryStore
from .models import Candidate, PlatformCall
from .ranking import rank
from .registry import PlatformRegistry
from .text import normalize
from .understanding import Understanding, understand

_TAB_ALIASES = {"all": "all", "novel": "novel", "drama": "short_drama", "short_drama": "short_drama"}


@dataclass
class SearchOutcome:
    trace_id: str
    query: str
    tab: str
    allowed: bool = True
    code: str = "ok"
    message: str = ""
    understanding: Understanding | None = None
    groups: dict[str, dict] = field(default_factory=dict)
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expanded: bool = False
    fallback_stage: int = 1
    elapsed_ms: int = 0
    budget_ms: int = 0
    platforms: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    guardrail_flags: list[str] = field(default_factory=list)
    personalization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "tab": self.tab,
            "allowed": self.allowed,
            "code": self.code,
            "message": self.message,
            "understanding": self.understanding.to_dict() if self.understanding else {},
            "groups": self.groups,
            "degraded": self.degraded,
            "degraded_reasons": self.degraded_reasons,
            "warnings": self.warnings,
            "expanded": self.expanded,
            "fallback_stage": self.fallback_stage,
            "elapsed_ms": self.elapsed_ms,
            "budget_ms": self.budget_ms,
            "platforms": self.platforms,
            "notes": self.notes,
            "suggestions": self.suggestions,
            "guardrail_flags": self.guardrail_flags,
            "personalization": self.personalization,
        }


class SearchAgent:
    def __init__(self, settings: Settings, corpus: Corpus, registry: PlatformRegistry,
                 hub: AdapterHub, memory: MemoryStore, guardrails: Guardrails, audit: AuditLog):
        self.settings = settings
        self.corpus = corpus
        self.registry = registry
        self.hub = hub
        self.memory = memory
        self.guardrails = guardrails
        self.audit = audit
        self._writers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nds-mem")
        self._priority = {pid: p.defn.priority for pid, p in registry.platforms.items()}

    # ------------------------------------------------------------------ public
    def search(self, query: str, tab: str = "all", user_id: str = "u_demo",
               session_id: str | None = None, limit: int | None = None,
               budget_ms: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        trace_id = uuid.uuid4().hex[:12]
        session_id = session_id or f"s_{trace_id}"
        tab = _TAB_ALIASES.get((tab or "all").lower(), "all")
        budget_ms = int(budget_ms or self.settings.budget_ms)

        profile = self.memory.get_profile(user_id) if self.memory else {}
        screen = self.guardrails.screen(query, user_id, profile.get("blocked_topics"))
        if not screen.allowed:
            outcome = SearchOutcome(
                trace_id=trace_id, query=screen.query, tab=tab, allowed=False,
                code=screen.code, message=screen.message, suggestions=screen.suggestions,
                guardrail_flags=screen.flags, budget_ms=budget_ms,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            self._write_audit(trace_id, user_id, screen.redacted, tab, outcome, 0)
            return outcome.to_dict()

        uq = understand(screen.query, self.corpus, tab)
        kinds = [k for k in ("novel", "short_drama") if k in uq.types]
        if not kinds:
            kinds = ["novel", "short_drama"]

        candidates: dict[str, list[Candidate]] = {k: [] for k in kinds}
        calls: dict[str, PlatformCall] = {}
        blocked = set(profile.get("blocked_topics") or [])
        filtered_count = 0

        stage = 1
        fallback_stage = 1
        reasons: list[str] = []
        warnings: list[str] = []
        source_failed = False
        budget_exhausted = False
        notes = list(uq.notes)
        plan = {k: self.registry.plan(k) for k in kinds}
        max_stages = max((len(v) for v in plan.values()), default=1)

        while stage <= max_stages:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            remaining = budget_ms - elapsed_ms
            todo = []
            for k in kinds:
                if stage > len(plan[k]) or self._sufficient(k, candidates[k], uq):
                    continue
                for pid in plan[k][stage - 1]:
                    rt = self.registry.get(pid)
                    if pid in calls and calls[pid].used:
                        continue
                    if rt is not None and not rt.can_try():
                        if not rt.defn.enabled:
                            reason = "平台未启用（需配置后接入）"
                        elif rt.forced_down:
                            reason = "演示中平台被停用"
                        elif rt.breaker.state == "half_open":
                            reason = "半开试探进行中，跳过"
                        else:
                            reason = "熔断打开，冷却中跳过"
                        calls.setdefault(pid, PlatformCall(platform=pid, state=rt.state_label(), reason=reason))
                        if rt.forced_down or rt.breaker.state in ("open", "half_open"):
                            source_failed = True
                            reasons.append(f"{pid} 不可用（{reason}）")
                        continue
                    todo.append((k, pid))
            if not todo:
                if stage >= max_stages:
                    break
                stage += 1
                continue
            if remaining <= 50:
                budget_exhausted = True
                reasons.append("预算耗尽，返回已召回的部分结果")
                notes.append(f"在第 {stage} 级降级时预算耗尽")
                break

            fallback_stage = stage
            results = self._recall_stage(todo, uq, remaining)
            stage_used = False
            for (kind, pid), (cands, latency, error) in results.items():
                call = calls.setdefault(pid, PlatformCall(platform=pid))
                call.used = True
                call.latency_ms = max(call.latency_ms, latency)
                if error:
                    call.state = "degraded" if "timeout" in error else "unavailable"
                    call.reason = error
                    source_failed = True
                    self.registry.get(pid).report_failure(error)
                    reasons.append(f"{pid} 不可用（{error}）")
                    continue
                call.state = self.registry.get(pid).state_label()
                self.registry.get(pid).report_success(latency)
                call.candidates += len(cands)
                stage_used = True
                for cand in cands:
                    if blocked and blocked & (set(cand.work.themes) | set(cand.work.tags)):
                        filtered_count += 1
                        continue
                    candidates[kind].append(cand)
            if stage_used:
                self._merge_procedure_hint(notes, uq)
            if stage == 1 and not stage_used:
                notes.append("主源全部不可用，直接进入降级链")
            if all(self._sufficient(k, candidates[k], uq) for k in kinds):
                break
            # 已经拿到确定性命中（逐字句子 / 精确剧名 / 主角名）就不再消耗预算扩源
            if any(self._definitive(k, candidates[k], uq) for k in kinds):
                break
            stage += 1

        # 熔断打开/半开占用的源标注为跳过（只读探测，不占用试探名额）
        for pid, rt in self.registry.platforms.items():
            if self._kind_of(rt) not in kinds:
                continue
            if pid in calls or rt.available():
                continue
            if not rt.defn.enabled:
                reason = "平台未启用（需配置后接入）"
            elif rt.forced_down:
                reason = "演示中平台被停用"
            elif rt.breaker.state == "half_open":
                reason = "半开试探进行中，跳过"
            else:
                reason = "熔断打开，冷却中跳过"
            calls[pid] = PlatformCall(platform=pid, state=rt.state_label(), reason=reason)

        # 结果组装
        groups: dict[str, dict] = {}
        per_group_limit = limit or (self.settings.max_results_all if tab == "all"
                                    else self.settings.max_results_per_group)
        for kind in kinds:
            results = rank(candidates[kind], kind, profile, self._priority, per_group_limit)
            groups[kind] = {
                "label": "短篇小说" if kind == "novel" else "短剧",
                "total": len(results),
                "results": results,
            }
            if not results:
                notes.append(f"{'短篇小说' if kind == 'novel' else '短剧'} 暂无命中")

        total = sum(g["total"] for g in groups.values())
        has_definitive = any(self._definitive(k, candidates[k], uq) for k in kinds)
        if total < self.settings.min_results and kinds and not has_definitive:
            warnings.append(f"结果较少（{total} < {self.settings.min_results}），已尝试全部可用平台")
        if fallback_stage > 1 and not has_definitive:
            warnings.append(f"主源结果不足，已按降级链拓展到第 {fallback_stage} 级备用源")
        if filtered_count:
            warnings.append(f"已按你的屏蔽主题过滤 {filtered_count} 条")
        if any(c.state in ("unavailable", "degraded") for c in calls.values()):
            notes.append("部分平台降级，结果可能不完整")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        outcome = SearchOutcome(
            trace_id=trace_id,
            query=screen.query,
            tab=tab,
            understanding=uq,
            groups=groups,
            degraded=source_failed or budget_exhausted,
            degraded_reasons=list(dict.fromkeys(reasons + warnings)),
            warnings=list(dict.fromkeys(warnings)),
            expanded=fallback_stage > 1,
            fallback_stage=fallback_stage,
            elapsed_ms=elapsed_ms,
            budget_ms=budget_ms,
            platforms=[c.to_dict() for c in sorted(calls.values(), key=lambda c: c.platform)],
            notes=list(dict.fromkeys(notes)),
            guardrail_flags=screen.flags,
            personalization={
                "personalized": bool(profile.get("preferred_themes") or profile.get("clicked_works")),
                "themes": profile.get("preferred_themes", []),
                "clicked": len(profile.get("clicked_works", {})),
            },
        )
        self._write_memory(user_id, session_id, screen.query, uq, fallback_stage)
        self._write_audit(trace_id, user_id, screen.redacted, tab, outcome, total)
        return outcome.to_dict()

    @staticmethod
    def _kind_of(rt) -> str:
        return "novel" if rt.defn.type == "novel" else "short_drama"

    def detail(self, work_id: str) -> dict[str, Any] | None:
        work = self.corpus.get(work_id)
        if not work:
            return None
        card = work.to_card()
        card["sentences"] = [s.to_dict() for s in work.sentences[:20]]
        card["sources"] = [s.to_dict() for s in work.sources]
        return card

    def platform_report(self) -> list[dict[str, Any]]:
        return self.registry.snapshot()

    def stats(self) -> dict[str, Any]:
        return {
            "corpus": self.corpus.stats(),
            "platforms": self.registry.snapshot(),
            "procedures": self.memory.procedures(10) if self.memory else [],
            "settings": {
                "budget_ms": self.settings.budget_ms,
                "min_results": self.settings.min_results,
                "rate_limit_per_min": self.settings.rate_limit_per_min,
            },
        }

    def close(self, wait: bool = True) -> None:
        """关闭后台写入线程；wait=True 保证记忆/审计落盘后再释放 SQLite。"""
        try:
            self._writers.shutdown(wait=wait, cancel_futures=not wait)
        except TypeError:                     # Python < 3.9
            self._writers.shutdown(wait=wait)

    # ----------------------------------------------------------------- internal
    def _recall_stage(self, todo: list[tuple[str, str]], uq: Understanding,
                      remaining_ms: int) -> dict[tuple[str, str], tuple[list[Candidate], int, str]]:
        out: dict[tuple[str, str], tuple[list[Candidate], int, str]] = {}
        executor = ThreadPoolExecutor(max_workers=max(1, min(8, len(todo))), thread_name_prefix="nds-recall")
        futures = {executor.submit(self._call_platform, kind, pid, uq): (kind, pid) for kind, pid in todo}
        # 单级等待上限跟随参与平台中最长的 timeout（真实聚合源通常 1–3s），
        # 仍受全局剩余预算约束，保证"超时必有结果"。
        stage_timeout_ms = max((self.registry.get(pid).defn.timeout_ms for _, pid in todo), default=800)
        timeout_s = max(0.05, min(remaining_ms, stage_timeout_ms + 100) / 1000)
        try:
            done, not_done = wait(futures, timeout=timeout_s)
        finally:
            for future in not_done:
                kind, pid = futures[future]
                future.cancel()
                out[(kind, pid)] = ([], int(timeout_s * 1000), "timeout: 超过单级预算")
            # 不等待残留线程（真实适配器自带 socket 超时）
            executor.shutdown(wait=False, cancel_futures=True)
        for future in done:
            kind, pid = futures[future]
            try:
                cands, latency = future.result()
                out[(kind, pid)] = (cands, latency, "")
            except AdapterError as exc:
                out[(kind, pid)] = ([], 0, str(exc))
            except Exception as exc:  # noqa: BLE001 适配器任何异常都归为平台失败
                out[(kind, pid)] = ([], 0, f"{type(exc).__name__}: {exc}")
        return out

    def _call_platform(self, kind: str, platform_id: str, uq: Understanding) -> tuple[list[Candidate], int]:
        rt = self.registry.get(platform_id)
        if rt is None:
            raise AdapterError("platform_not_registered")
        if rt.defn.type != {"novel": "novel", "short_drama": "drama"}.get(kind, kind):
            return [], 0
        if rt.simulated_latency_ms:
            time.sleep(rt.simulated_latency_ms / 1000)
        adapter = self.hub.get(platform_id, rt.defn)
        started = time.perf_counter()
        cands = adapter.search(uq, kind)
        latency = int((time.perf_counter() - started) * 1000)
        if rt.simulated_latency_ms:
            latency = max(latency, rt.simulated_latency_ms)
        return cands, latency

    @staticmethod
    def _layer_counts(cands: list[Candidate], kind: str) -> dict[str, int]:
        best: dict[str, int] = {}
        for cand in cands:
            layer = layer_of(cand.channel, kind)
            best[cand.work.id] = min(best.get(cand.work.id, 9), layer)
        return best

    @staticmethod
    def _definitive(kind: str, cands: list[Candidate], uq: Understanding) -> bool:
        """命中即"确定性结果"，无需为凑数量继续扩源。

        例如：引号句子在番茄正文里逐字命中、剧名精确命中、主角名命中。
        """
        channels = {c.channel for c in cands if c.work.type == kind}
        if uq.intent == "sentence":
            return bool(channels & {"sentence_exact", "line_exact"})
        if uq.intent == "title":
            return bool(channels & {"title_exact", "name_exact"})
        if uq.intent == "character":
            return bool(channels & {"character", "cast"})
        if uq.intent == "mixed":
            return bool(channels & {"sentence_exact", "line_exact", "name_exact", "title_exact"})
        return False

    def _sufficient(self, kind: str, cands: list[Candidate], uq: Understanding) -> bool:
        if not cands:
            return False
        if self._definitive(kind, cands, uq):
            return True
        # 主题查询的"结果足量"阈值放宽到主题层（3），标题放宽到标题层（4）
        threshold = {"sentence": 1, "character": 2, "theme": 3, "title": 4}.get(uq.intent, 2)
        layers = self._layer_counts(cands, kind)
        return sum(1 for layer in layers.values() if layer <= threshold) >= self.settings.min_results

    def _merge_procedure_hint(self, notes: list[str], uq: Understanding) -> None:
        if not self.memory:
            return
        pattern = normalize(uq.raw)[:24]
        prior = self.memory.procedure_stage(pattern)
        if prior and prior > 1:
            notes.append(f"历史同类查询曾降级到第 {prior} 级（程序记忆）")

    def _write_memory(self, user_id: str, session_id: str, query: str,
                      uq: Understanding, fallback_stage: int) -> None:
        if not self.memory:
            return
        pattern = normalize(uq.raw)[:24]
        try:
            self._writers.submit(self.memory.record_search, user_id, session_id, query,
                                 uq.themes, uq.types)
            self._writers.submit(self.memory.record_procedure, pattern, fallback_stage, "")
        except RuntimeError:
            pass

    def _write_audit(self, trace_id: str, user_id: str, query: str, tab: str,
                     outcome: SearchOutcome, total: int) -> None:
        try:
            self._writers.submit(
                self.audit.write,
                {
                    "trace_id": trace_id,
                    "event": "search",
                    "user_id": user_id,
                    "query": query,
                    "tab": tab,
                    "allowed": outcome.allowed,
                    "code": outcome.code,
                    "degraded": outcome.degraded,
                    "fallback_stage": outcome.fallback_stage,
                    "results": total,
                    "elapsed_ms": outcome.elapsed_ms,
                    "flags": outcome.guardrail_flags,
                },
            )
        except RuntimeError:
            pass
