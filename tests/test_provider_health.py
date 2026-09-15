from app.providers.crossref import CrossrefProvider
from app.providers.more import _CrossrefFilterProvider
from app.services.provider_health import CircuitState, ProviderHealthRegistry, provider_health


def test_crossref_profiles_share_rate_limit_group():
    class Mdpi(_CrossrefFilterProvider):
        name = "mdpi"
        display_name = "MDPI"

    assert CrossrefProvider().rate_limit_group == "crossref"
    assert Mdpi().rate_limit_group == "crossref"


def test_circuit_opens_after_repeated_failures():
    registry = ProviderHealthRegistry(failure_threshold=3, cooldown_seconds=30)
    assert registry.allow("crossref") is True
    registry.record_failure("crossref", status_code=503)
    registry.record_failure("crossref", status_code=503)
    assert registry.allow("crossref") is True
    registry.record_failure("crossref", status_code=503)
    assert registry.stats("crossref").circuit == CircuitState.OPEN
    assert registry.allow("crossref") is False


def test_global_registry_reset():
    provider_health().record_failure("unit-test-upstream", status_code=500)
    provider_health().reset()
    assert provider_health().snapshot() == []
