#!/usr/bin/env python3
"""Unit tests for remote_client module."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from remote_client import (
    RemoteModelRegistry,
    Model,
    RemoteAPIError,
    ModelNotFoundError,
)


class TestModel:
    """Tests for the Model dataclass."""
    
    def test_model_from_dict(self):
        """Test creating a Model from a dictionary."""
        data = {
            "name": "test-model",
            "repo": "user/repo",
            "filename": "model.gguf",
            "expected_size": 1000000000,
            "port": 8080,
            "threads": 8,
        }
        model = Model.from_dict(data)
        
        assert model.name == "test-model"
        assert model.repo == "user/repo"
        assert model.filename == "model.gguf"
        assert model.expected_size == 1000000000
        assert model.port == 8080
        assert model.threads == 8
    
    def test_model_defaults(self):
        """Test Model default values."""
        model = Model(name="test")
        
        assert model.repo == ""
        assert model.threads == 16
        assert model.mlock is True
        assert model.flash_attn == "off"


class TestRemoteModelRegistry:
    """Tests for RemoteModelRegistry."""
    
    def test_init(self):
        """Test initialization of RemoteModelRegistry."""
        client = RemoteModelRegistry("http://localhost:8080")
        
        assert client.base_url == "http://localhost:8080"
        assert client.timeout == 30
        assert client.max_retries == 3
    
    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from base_url."""
        client = RemoteModelRegistry("http://localhost:8080/")
        
        assert client.base_url == "http://localhost:8080"
    
    @patch("remote_client.requests.Session")
    def test_list_models(self, mock_session_class):
        """Test listing models with OpenAI format response."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "data": [
                {"id": "model1"},
                {"id": "model2"},
            ]
        }
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        models = client.list_models()
        
        assert len(models) == 2
        assert models[0].name == "model1"
        assert models[1].name == "model2"
    
    @patch("remote_client.requests.Session")
    def test_list_models_dict_response(self, mock_session_class):
        """Test listing models with OpenAI format dict response."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "data": [
                {"id": "model1"}
            ]
        }
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        models = client.list_models()
        
        assert len(models) == 1
        assert models[0].name == "model1"
    
    @patch("remote_client.requests.Session")
    def test_load_model(self, mock_session_class):
        """Test loading a model - checks if model exists in list."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "data": [{"id": "test-model"}]
        }
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        result = client.load_model("test-model")
        
        assert result is True
        # load_model calls list_models to check if model exists
        assert mock_session.request.call_count >= 1
    
    @patch("remote_client.requests.Session")
    def test_load_model_not_found(self, mock_session_class):
        """Test loading a model that doesn't exist."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "data": []
        }
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        with pytest.raises(ModelNotFoundError):
            client.load_model("nonexistent")
    
    @patch("remote_client.requests.Session")
    def test_unload_model(self, mock_session_class):
        """Test unloading a model - checks if model exists in list."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "data": [{"id": "test-model"}]
        }
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        result = client.unload_model("test-model")
        
        assert result is True
    
    @patch("remote_client.requests.Session")
    def test_unload_model_not_found(self, mock_session_class):
        """Test unloading a model that doesn't exist."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "data": []
        }
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        with pytest.raises(ModelNotFoundError):
            client.unload_model("nonexistent")
    
    @patch("remote_client.requests.Session")
    def test_get_model_not_found(self, mock_session_class):
        """Test getting a model that doesn't exist in the list."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "data": []
        }
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        result = client.get_model("nonexistent")
        
        assert result is None
    
    @patch("remote_client.requests.Session")
    def test_get_status(self, mock_session_class):
        """Test getting server status - calls root endpoint."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>llama.cpp server</body></html>"
        mock_session.request.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        client = RemoteModelRegistry("http://localhost:8080")
        status = client.get_status()
        
        assert status["status"] == "running"
        # get_status calls the root endpoint
        mock_session.request.assert_called_once_with(
            "GET",
            "http://localhost:8080/",
            timeout=30,
        )
    
    @patch("remote_client.requests.Session")
    def test_context_manager(self, mock_session_class):
        """Test using RemoteModelRegistry as context manager."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        
        with RemoteModelRegistry("http://localhost:8080") as client:
            assert client.base_url == "http://localhost:8080"
        
        mock_session.close.assert_called_once()


class TestExceptions:
    """Tests for custom exceptions."""
    
    def test_remote_api_error(self):
        """Test RemoteAPIError exception."""
        error = RemoteAPIError("Test error", status_code=500)
        
        assert str(error) == "Test error"
        assert error.status_code == 500
    
    def test_model_not_found_error(self):
        """Test ModelNotFoundError exception."""
        error = ModelNotFoundError("test-model")
        
        assert "test-model" in str(error)
        assert error.model_name == "test-model"
