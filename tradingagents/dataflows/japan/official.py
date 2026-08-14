"""Factory for Phase-2 official Japan providers."""

from .company_ir import CompanyIRProvider
from .edinet import EDINETProvider
from .expectations import JapanAnalystExpectationsProvider
from .jpx import JPXProvider
from .jquants import JQuantsProvider
from .jsf import JSFProvider
from .macro import JapanMacroProvider
from .news import JapanNewsProvider
from .sentiment import JapanSentimentProvider
from .tdnet import TDnetProvider


def build_official_japan_providers():
    """Return providers in official-fact priority order for JapanDataService."""
    return (
        JQuantsProvider(),
        JPXProvider(),
        TDnetProvider(),
        EDINETProvider(),
        CompanyIRProvider(),
        JSFProvider(),
        JapanMacroProvider(),
        JapanNewsProvider(),
        JapanSentimentProvider(),
        JapanAnalystExpectationsProvider(),
    )
