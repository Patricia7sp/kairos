"""An accessible public catalog must not certify an invalid credential."""

import httpx
import pytest

from kairos_providers.adapters.openrouter import OpenRouterAdapter


@pytest.mark.parametrize("valid", [False, True])
def test_connection_authenticates_key_before_public_catalog(valid):
    import asyncio

    paths = []

    def upstream(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/key":
            assert request.headers["authorization"] == "Bearer test-credential"
            if not valid:
                return httpx.Response(401, json={"error": {"message": "private sentinel"}})
            return httpx.Response(200, json={"data": {"label": "private sentinel"}})
        return httpx.Response(200, json={"data": []})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
            result = await OpenRouterAdapter(http, "test-credential").test_connection()
        assert result.ok is valid
        assert "private sentinel" not in result.message
        assert paths == (["/api/v1/key", "/api/v1/models"] if valid else ["/api/v1/key"])

    asyncio.run(check())


def test_privacy_policy_rejection_is_actionable_without_echoing_upstream_secrets():
    import asyncio

    from kairos_providers import AdapterRequest, ProviderError, ProviderModelRef

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    404,
                    json={
                        "error": {
                            "message": "No endpoints found matching your data policy (Free model training). private-sentinel"
                        }
                    },
                )
            )
        ) as client:
            with pytest.raises(ProviderError) as caught:
                async for _ in OpenRouterAdapter(client, "test").stream(
                    AdapterRequest(
                        model=ProviderModelRef("openrouter", "test/free"),
                        messages=(),
                    )
                ):
                    pass
            assert caught.value.kind == "policy"
            assert caught.value.retryable is False
            assert "privacidade" in str(caught.value)
            assert "private-sentinel" not in str(caught.value)

    asyncio.run(run())
