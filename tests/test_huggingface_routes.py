"""Regression tests for Hugging Face model search routes."""

from unittest.mock import Mock, patch

import pytest
from flask import Flask

from webapp.routes.huggingface import hf_bp


@pytest.mark.parametrize(
    "hf_models",
    [
        [],
        [{"id": "author/model", "siblings": [{"rfilename": "README.md"}]}],
    ],
)
@patch("webapp.models.get_models", return_value={"local-model": {}})
@patch("webapp.routes.huggingface.requests.get")
def test_repo_search_without_gguf_results_returns_empty_success(
    mock_hf_get, _mock_get_models, hf_models
):
    hf_response = Mock(status_code=200)
    hf_response.json.return_value = hf_models
    mock_hf_get.return_value = hf_response

    app = Flask(__name__)
    app.register_blueprint(hf_bp)
    app.config["TESTING"] = True

    response = app.test_client().post(
        "/api/hf-repo-search", json={"model": "local-model"}
    )

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "results": []}
