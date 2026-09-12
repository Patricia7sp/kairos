"""Copy immutable canonical values only at the provider wire boundary."""

import math
from collections.abc import Mapping
from typing import Any

from kairos_providers.adapter_contract import ProviderError, ProviderErrorKind


def wire_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        return {key: wire_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
