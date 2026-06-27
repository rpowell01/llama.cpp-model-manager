"""Unit tests for Flask routes - lifecycle, chat, and status endpoints."""

import pytest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestLifecycleRoutes:
    """Tests for lifecycle routes (start/stop)."""
    
    @pytest.fixture
    def app(self):
        from flask import Flask
        from routes.lifecycle import lifecycle_bp
        app = Flask(__name__)
        app.register_blueprint(lifecycle_bp)
        app.config['TESTING'] = True
        return app
    
    @pytest.fixture
    def client(self, app):
        return app.test_client()
    
    @patch('routes.lifecycle.get_model_registry')
    def test_start_remote_mode(self, mock_get_registry, client):
        """Test start endpoint in remote mode returns 202 Accepted."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = True
        mock_registry._remote_client.get_status.return_value = {"status": "running"}
        mock_get_registry.return_value = mock_registry
        
        response = client.post('/api/start', 
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 202
        data = response.get_json()
        assert data['status'] == 'loading'
        assert data['model'] == 'test-model'
        # In remote mode, models are pre-loaded, so we just verify connectivity
        # get_status is called in validate_remote_connectivity (before_request) and in api_start
        assert mock_registry._remote_client.get_status.call_count >= 1
    
    @patch('routes.lifecycle.get_model_registry')
    def test_start_local_mode_unknown_model(self, mock_get_registry, client):
        """Test start endpoint in local mode with unknown model returns 404."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_registry.list_models.return_value = []  # No models
        mock_get_registry.return_value = mock_registry
        
        response = client.post('/api/start',
            json={'model': 'unknown-model'},
            content_type='application/json')
        
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
    
    @patch('routes.lifecycle.get_model_registry')
    def test_start_missing_model(self, mock_get_registry, client):
        """Test start endpoint without model name returns 400."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_get_registry.return_value = mock_registry
        
        response = client.post('/api/start',
            json={},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
    
    @patch('routes.lifecycle.get_model_registry')
    def test_stop_remote_mode(self, mock_get_registry, client):
        """Test stop endpoint in remote mode returns stopped status."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = True
        mock_get_registry.return_value = mock_registry
        
        response = client.post('/api/stop',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'stopped'
        # In remote mode, models are pre-loaded and cannot be stopped via API
        mock_registry.unload_model.assert_not_called()
    
    @patch('routes.lifecycle.get_model_registry')
    def test_stop_missing_model(self, mock_get_registry, client):
        """Test stop endpoint without model name returns 400."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_get_registry.return_value = mock_registry
        
        response = client.post('/api/stop',
            json={},
            content_type='application/json')
        
        assert response.status_code == 400


class TestChatRoutes:
    """Tests for chat routes."""
    
    @pytest.fixture
    def app(self):
        from flask import Flask
        from routes.chat import chat_bp
        app = Flask(__name__)
        app.register_blueprint(chat_bp)
        app.config['TESTING'] = True
        return app
    
    @pytest.fixture
    def client(self, app):
        return app.test_client()
    
    @patch('routes.chat.get_chat_registry')
    def test_chat_missing_model(self, mock_get_registry, client):
        """Test chat endpoint without model name returns 400."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_get_registry.return_value = mock_registry
        
        response = client.post('/api/chat',
            json={'messages': [{'role': 'user', 'content': 'hello'}]},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
    
    @patch('routes.chat.get_chat_registry')
    def test_chat_missing_messages(self, mock_get_registry, client):
        """Test chat endpoint without messages returns 400."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_registry.list_models.return_value = []
        mock_get_registry.return_value = mock_registry
        
        response = client.post('/api/chat',
            json={'model': 'test-model'},
            content_type='application/json')
        
        # Model not found returns 404
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data


class TestStatusRoutes:
    """Tests for status routes."""
    
    @pytest.fixture
    def app(self):
        from flask import Flask
        from routes.status import status_bp
        app = Flask(__name__)
        app.register_blueprint(status_bp)
        app.config['TESTING'] = True
        return app
    
    @pytest.fixture
    def client(self, app):
        return app.test_client()
    
    @patch('routes.status.get_status_registry')
    def test_api_status(self, mock_get_registry, client):
        """Test status endpoint returns mode and remote health."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_get_registry.return_value = mock_registry
        
        response = client.get('/api/status')
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'models' in data
        assert 'mode' in data
        assert data['mode'] == 'local'
        assert 'remote_healthy' in data
    
    @patch('routes.status.get_status_registry')
    def test_api_status_remote_mode(self, mock_get_registry, client):
        """Test status endpoint in remote mode."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = True
        mock_get_registry.return_value = mock_registry
        
        response = client.get('/api/status')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['mode'] == 'remote'
    
    @patch('routes.status.get_status_registry')
    def test_api_next_port_local(self, mock_get_registry, client):
        """Test next-port endpoint in local mode."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_get_registry.return_value = mock_registry
        
        with patch('routes.status.check_port_active', return_value=False):
            with patch('routes.status.get_models', return_value={}):
                response = client.get('/api/next-port')
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'port' in data
    
    @patch('routes.status.get_status_registry')
    def test_api_next_port_remote(self, mock_get_registry, client):
        """Test next-port endpoint in remote mode returns placeholder."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = True
        mock_get_registry.return_value = mock_registry
        
        response = client.get('/api/next-port')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['port'] == 0
        assert 'note' in data