"""Factory for Phase-2 official Japan providers."""

from .company_ir import CompanyIRProvider
from .edinet import EDINETProvider
from .jpx import JPXProvider
from .jquants import JQuantsProvider
from .jsf import JSFProvider
from .tdnet import TDnetProvider


def build_official_japan_providers():
    """Return providers in official-fact priority order for JapanDataService."""
    return (JQuantsProvider(), JPXProvider(), TDnetProvider(), EDINETProvider(), CompanyIRProvider(), JSFProvider())
