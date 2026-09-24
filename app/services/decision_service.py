from __future__ import annotations

from app.domain.evidence import EvidenceRecord, FactType
from app.domain.intent import RouteIntent
from app.domain.policies import POLICY_VERSION
from app.domain.report import DecisionReport, DecisionStatus, ExecutionMode
from app.domain.verifier import VERIFIER_VERSION, RouteVerifier
from app.providers.contracts import EvidenceProvider, SearchProvider


class DecisionService:
    """确定性检索-取证-验证闭环；后续 Worker 只替换调度边界，不替换验证规则。"""

    def __init__(
        self,
        search_provider: SearchProvider,
        evidence_provider: EvidenceProvider,
        verifier: RouteVerifier | None = None,
    ) -> None:
        self.search_provider = search_provider
        self.evidence_provider = evidence_provider
        self.verifier = verifier or RouteVerifier()

    async def decide(
        self,
        intent: RouteIntent,
        *,
        candidate_limit: int = 10,
        run_id: str | None = None,
        execution_mode: ExecutionMode = ExecutionMode.DETERMINISTIC_PREVIEW,
    ) -> DecisionReport:
        candidates = await self.search_provider.search(intent, limit=candidate_limit)
        fact_fields = intent.required_fact_fields()
        typed_fact_types = [FactType(value) for value in sorted(fact_fields)]
        records: list[EvidenceRecord] = []
        warnings: list[str] = []

        if not candidates:
            # 搜索空结果属于正常业务结论，不视为系统异常。
            warnings.append("no_candidate_found")
        elif not typed_fact_types:
            # 无可验证约束时不做“已验证推荐”，避免空约束被误读为验证通过。
            warnings.append("request_has_no_verifiable_constraints")
        else:
            try:
                records = await self.evidence_provider.collect(
                    [candidate.route_id for candidate in candidates], typed_fact_types
                )
            except Exception as exc:  # provider 隔离：依赖故障不伪造验证结果
                warnings.append(f"evidence_provider_unavailable:{type(exc).__name__}")
            if not records:
                warnings.append("no_evidence_returned")

        reports = [self.verifier.verify(intent, candidate, records) for candidate in candidates]
        reports.sort(key=lambda report: (not report.hard_pass, -report.soft_score, report.route_id))

        if not candidates:
            status = DecisionStatus.NO_MATCH
            verified: list[str] = []
        elif not typed_fact_types:
            status = DecisionStatus.INSUFFICIENT_EVIDENCE
            verified = []
        else:
            status = DecisionReport.status_from_candidates(reports)
            verified = [report.route_id for report in reports if report.hard_pass]

        return DecisionReport(
            run_id=run_id,
            status=status,
            execution_mode=execution_mode,
            intent_fingerprint=intent.fingerprint(),
            policy_version=f"{POLICY_VERSION}+{VERIFIER_VERSION}",
            candidates=reports,
            verified_route_ids=verified,
            warnings=warnings,
            budget={
                "candidate_count": len(candidates),
                "fact_type_count": len(typed_fact_types),
                "evidence_count": len(records),
                "provider_calls": {
                    "search": getattr(self.search_provider, "search_calls", None),
                    "evidence": getattr(self.evidence_provider, "evidence_calls", None),
                },
            },
        )
