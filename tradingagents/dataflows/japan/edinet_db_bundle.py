"""Japan-bundle adapter for normalized EDINET DB financial documents.

The adapter is intentionally thin: issuer resolution, transport, latest-period
selection, and financial normalization remain owned by the existing EDINET DB
modules.  Only the normalized ``FinancialDocument`` enters bundle metadata;
raw API responses and transport diagnostics never become research context.
"""

from __future__ import annotations

from datetime import date

from tradingagents.dataflows.market import Market, MarketContext

from .edinet_db import (
    EDINETDBProvider,
    EDINETDBResponse,
    EDINETDBStatus,
    IssuerIdentityStatus,
)
from .edinet_db_service import EDINETDBFinancialService, EDINETDBFinancialSnapshot
from .models import DataStatus, ProviderResponse, SourceStatus


class EDINETDBFinancialBundleProvider:
    """Collect one latest-as-of normalized EDINET DB financial document."""

    name = "EDINET DB Financials"
    category = "fundamentals"
    # v3 invalidates snapshots produced before forecast-only disclosures were
    # excluded from Actual/FY-Guidance selection (Stage 10 evidence contract).
    cache_version = "3"

    def __init__(
        self,
        provider: EDINETDBProvider | None = None,
        financial_service: EDINETDBFinancialService | None = None,
    ) -> None:
        self.provider = provider or EDINETDBProvider()
        self.financial_service = financial_service or EDINETDBFinancialService(self.provider)

    async def fetch(
        self,
        context: MarketContext,
        *,
        start_date: str,
        end_date: str,
    ) -> ProviderResponse:
        del start_date
        if context.market != Market.JP:
            return self._unavailable("NON_JP_MARKET")
        try:
            analysis_as_of = date.fromisoformat(end_date)
        except (TypeError, ValueError):
            return self._unavailable("INVALID_ANALYSIS_AS_OF")

        identity = await self.provider.resolve_issuer_identity(context.native_symbol)
        if identity.status != IssuerIdentityStatus.OK or not identity.edinet_code:
            return self._unavailable(f"ISSUER_IDENTITY:{identity.detail or 'DATA_UNAVAILABLE'}")

        result = await self.financial_service.fetch_financial_snapshot(
            identity.edinet_code,
            analysis_as_of=analysis_as_of,
        )
        if isinstance(result, EDINETDBResponse):
            return self._transport_failure(result)
        if not isinstance(result, EDINETDBFinancialSnapshot):
            return self._unavailable("FINANCIAL_SNAPSHOT_DATA_UNAVAILABLE")

        actual = result.actual_document.to_dict() if result.actual_document else None
        guidance = result.guidance_document.to_dict() if result.guidance_document else None
        if actual is None and guidance is None and result.guidance_status != "NOT_PROVIDED":
            return self._unavailable(
                f"FINANCIAL_SNAPSHOT_DATA_UNAVAILABLE:{result.actual_detail or result.guidance_detail}"
            )

        return ProviderResponse(
            status=SourceStatus(
                self.name,
                DataStatus.OK,
                item_count=int(actual is not None) + int(guidance is not None),
            ),
            metadata={
                # Compatibility alias: the old slot always means Actual only.
                "financial_document": actual,
                "actual_document": actual,
                "guidance_document": guidance,
                "actual_status": result.actual_status,
                "guidance_status": result.guidance_status,
                "actual_detail": result.actual_detail,
                "guidance_detail": result.guidance_detail,
                "analysis_as_of": analysis_as_of.isoformat(),
                "actual_selection": {
                    "selection_basis": result.actual_selection.selection_basis,
                    "disclosure_date": result.actual_selection.disclosure_date,
                    "period_type": result.actual_selection.period_type,
                    "target_period_end": result.actual_selection.fiscal_year_end,
                    "detail": result.actual_selection.detail,
                },
                "guidance_selection": {
                    "selection_basis": result.guidance_selection.selection_basis,
                    "disclosure_date": result.guidance_selection.disclosure_date,
                    "period_type": result.guidance_selection.period_type,
                    "target_period_end": result.guidance_selection.target_period_end,
                    "detail": result.guidance_selection.detail,
                },
                "issuer_identity": {
                    "security_code": identity.security_code,
                    "source_security_code": identity.source_security_code,
                    "edinet_code": identity.edinet_code,
                    "source": identity.source,
                    "source_as_of": identity.source_as_of,
                },
            },
        )

    def _transport_failure(self, response: EDINETDBResponse) -> ProviderResponse:
        status = {
            EDINETDBStatus.AUTH_REQUIRED: DataStatus.AUTH_REQUIRED,
            EDINETDBStatus.RATE_LIMITED: DataStatus.RATE_LIMITED,
        }.get(response.status, DataStatus.DATA_UNAVAILABLE)
        detail = response.status.value
        if response.detail:
            detail += f":{response.detail}"
        return ProviderResponse(status=SourceStatus(self.name, status, detail=detail))

    def _unavailable(self, detail: str) -> ProviderResponse:
        return ProviderResponse(
            status=SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail=detail)
        )
