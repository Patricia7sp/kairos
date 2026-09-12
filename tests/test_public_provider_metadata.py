import json

import pytest

from kairos_web.message_metadata import public_message_accounting
from kairos_web.provider_api import public_parameters, public_profiles


@pytest.mark.parametrize("cost", [{"status": []}, {"source": {}}, {"estimated_usd": 10**1000}])
def test_malformed_cost_does_not_break_history(cost):
    value = public_message_accounting(json.dumps({"cost": cost}))
    assert value["cost"]["estimated_usd"] is None
    assert value["cost"]["source"] is None


def test_parameter_projection_drops_nested_secrets_and_invalid_profiles():
    assert public_parameters(
        {
            "temperature": {"api_key": "never-expose"},
            "seed": 7,
            "stop": ["ok", {"token": "never-expose"}],
        }
    ) == {"seed": 7}
    assert (
        public_profiles(
            {"profiles": {"bad": {"provider": {"api_key": "never-expose"}, "model": "x"}}}
        )
        == []
    )
