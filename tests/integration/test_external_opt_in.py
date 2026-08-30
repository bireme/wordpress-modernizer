import os

import pytest


@pytest.mark.integration
def test_external_contract_requires_explicit_opt_in() -> None:
    if os.environ.get("WP_MODERNIZER_INTEGRATION") != "1":
        pytest.skip("defina WP_MODERNIZER_INTEGRATION=1 apenas em laboratório de TESTE descartável")
    pytest.skip("a implantação deve fornecer fixtures de contrato específicas do laboratório")
