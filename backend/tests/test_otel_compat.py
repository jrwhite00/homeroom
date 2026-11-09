from importlib.metadata import version

# Dash0 operator 0.159.0 only auto-instruments Python when opentelemetry-api is 1.44.0.
# If this fails, the backend silently stops sending traces. See requirements.txt.
DASH0_OTEL_API_VERSION = "1.44.0"


def test_opentelemetry_api_matches_dash0_agent():
    assert version("opentelemetry-api") == DASH0_OTEL_API_VERSION
