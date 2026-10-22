"""Unit tests for Flask routes - lifecycle, chat, and status endpoints."""

import os
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
        mock_registry.list_models.return_value = []  # Return iterable
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


class TestUpdateModelMeta:
    """Tests for the /api/update-model-meta endpoint."""

    @pytest.fixture
    def app(self):
        from flask import Flask
        from routes.models import models_bp
        app = Flask(__name__)
        app.register_blueprint(models_bp)
        app.config['TESTING'] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def status_client(self):
        """Client for the status blueprint, which owns /api/status."""
        from flask import Flask
        from routes.status import status_bp
        status_app = Flask(__name__)
        status_app.register_blueprint(status_bp)
        status_app.config['TESTING'] = True
        return status_app.test_client()

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_speculative_decoding_model_is_saved(self, mock_get_models, mock_save_models, client):
        """Checking the Speculative Decoding Model box must persist the flag.

        Regression test: the field was missing from the endpoint's
        ``allowed_fields`` allowlist, so the value was silently dropped and
        the checkbox reverted to unchecked on the next refresh.
        """
        models = {'test-model': {'port': 11000, 'threads': 8}}
        mock_get_models.return_value = models

        response = client.post(
            '/api/update-model-meta',
            json={'model': 'test-model', 'speculative_decoding_model': True},
            content_type='application/json',
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data['changes']['speculative_decoding_model'] is True
        assert models['test-model']['speculative_decoding_model'] is True
        mock_save_models.assert_called_once()

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_speculative_decoding_model_can_be_unchecked(self, mock_get_models, mock_save_models, client):
        """Unchecking the box must persist False, not be dropped as a no-op."""
        models = {'test-model': {'port': 11000, 'speculative_decoding_model': True}}
        mock_get_models.return_value = models

        response = client.post(
            '/api/update-model-meta',
            json={'model': 'test-model', 'speculative_decoding_model': False},
            content_type='application/json',
        )

        assert response.status_code == 200
        assert models['test-model']['speculative_decoding_model'] is False

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_boolean_strings_are_normalized_not_truthiness_coerced(self, mock_get_models, mock_save_models, client):
        """String booleans must be parsed, not truthiness-coerced.

        bool("false") is True in Python, so a client sending the string
        "false" would otherwise persist True and silently invert the flag.
        """
        models = {'test-model': {'port': 11000}}
        mock_get_models.return_value = models

        for raw, expected in (('false', False), ('False', False), ('0', False),
                              ('true', True), ('True', True), ('1', True)):
            response = client.post(
                '/api/update-model-meta',
                json={'model': 'test-model', 'speculative_decoding_model': raw},
                content_type='application/json',
            )

            assert response.status_code == 200
            assert models['test-model']['speculative_decoding_model'] is expected, (
                f"{raw!r} should persist as {expected!r}"
            )

    @patch('routes.status.check_port_active', return_value=False)
    @patch('routes.status.get_models')
    @patch('routes.status.get_status_registry')
    def test_status_returns_speculative_decoding_model(self, mock_get_registry, mock_get_models,
                                                      mock_check_port, status_client):
        """The refresh path must return the flag so the panel can re-render it."""
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_get_registry.return_value = mock_registry
        mock_get_models.return_value = {
            'test-model': {'port': 11000, 'speculative_decoding_model': True},
        }

        response = status_client.get('/api/status')

        assert response.status_code == 200
        models = response.get_json()['models']
        assert models[0]['speculative_decoding_model'] is True

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_draft_model_path_is_stored_in_windows_form(self, mock_get_models, mock_save_models, client):
        """The draft model path must be stored in Windows form.

        llama-server.exe runs in a Windows context, so a WSL mount path
        (/mnt/c/...) has to be stored as C:/... in models.json.
        """
        models = {'test-model': {'port': 11000, 'threads': 8}}
        mock_get_models.return_value = models

        with patch('routes.models.normalize_path_for_binary',
                   return_value='C:/llama.cpp-models/draft.gguf') as mock_normalize:
            response = client.post(
                '/api/update-model-meta',
                json={'model': 'test-model', 'spec_draft_model': '/mnt/c/llama.cpp-models/draft.gguf'},
                content_type='application/json',
            )

        assert response.status_code == 200
        assert models['test-model']['spec_draft_model'] == 'C:/llama.cpp-models/draft.gguf'
        mock_normalize.assert_called_once_with('/mnt/c/llama.cpp-models/draft.gguf')

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_empty_draft_model_path_is_stored_as_empty(self, mock_get_models, mock_save_models, client):
        """Clearing the draft model must not run the path through normalisation."""
        models = {'test-model': {'port': 11000, 'spec_draft_model': 'C:/llama.cpp-models/draft.gguf'}}
        mock_get_models.return_value = models

        with patch('routes.models.normalize_path_for_binary') as mock_normalize:
            response = client.post(
                '/api/update-model-meta',
                json={'model': 'test-model', 'spec_draft_model': ''},
                content_type='application/json',
            )

        assert response.status_code == 200
        assert models['test-model']['spec_draft_model'] == ''
        mock_normalize.assert_not_called()

    @patch('routes.status.check_port_active', return_value=False)
    @patch('routes.status.get_models')
    @patch('routes.status.get_status_registry')
    def test_status_returns_model_path_in_binary_form(self, mock_get_registry, mock_get_models,
                                                      mock_check_port, status_client):
        """The status payload must expose the model path in binary form.

        The draft model dropdown offers these paths, and they have to match
        what is stored for the model (C:/... for a Windows llama-server.exe).
        """
        mock_registry = Mock()
        mock_registry.is_remote_mode = False
        mock_get_registry.return_value = mock_registry
        mock_get_models.return_value = {
            'test-model': {'port': 11000, 'model_path': '/mnt/c/llama.cpp-models/test.gguf'},
        }

        with patch('routes.status.convert_path_for_binary',
                   return_value='C:/llama.cpp-models/test.gguf'):
            response = status_client.get('/api/status')

        assert response.status_code == 200
        models = response.get_json()['models']
        assert models[0]['model_path_for_binary'] == 'C:/llama.cpp-models/test.gguf'

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_rating_zero_is_accepted(self, mock_get_models, mock_save_models, client):
        """A rating of 0 means "unrated" and must be saveable.

        The range used to be 1-5, which made it impossible to clear a rating
        once one had been set.
        """
        models = {'test-model': {'port': 11000, 'rating_speed': 3, 'rating_coding': 4}}
        mock_get_models.return_value = models

        response = client.post(
            '/api/update-model-meta',
            json={'model': 'test-model', 'rating_speed': 0, 'rating_coding': 0},
            content_type='application/json',
        )

        assert response.status_code == 200
        assert models['test-model']['rating_speed'] == 0
        assert models['test-model']['rating_coding'] == 0

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_rating_bounds_are_enforced(self, mock_get_models, mock_save_models, client):
        """Ratings outside 0-5 must be rejected."""
        models = {'test-model': {'port': 11000}}
        mock_get_models.return_value = models

        for field in ('rating_speed', 'rating_coding'):
            for bad in (-1, 6):
                response = client.post(
                    '/api/update-model-meta',
                    json={'model': 'test-model', field: bad},
                    content_type='application/json',
                )

                assert response.status_code == 400, f"{field}={bad} should be rejected"
                assert field not in models['test-model']

    @patch('routes.models.save_models')
    @patch('routes.models.get_models')
    def test_rating_string_values_are_coerced(self, mock_get_models, mock_save_models, client):
        """The UI sends number inputs as strings; they must still be accepted."""
        models = {'test-model': {'port': 11000}}
        mock_get_models.return_value = models

        response = client.post(
            '/api/update-model-meta',
            json={'model': 'test-model', 'rating_speed': '0', 'rating_coding': '5'},
            content_type='application/json',
        )

        assert response.status_code == 200
        assert models['test-model']['rating_speed'] == 0
        assert models['test-model']['rating_coding'] == 5


class TestGetMetrics:
    """Tests for the /api/get-metrics endpoint used by the performance graph.

    A llama-server that is busy processing a long prompt does not answer
    /metrics within the request timeout. The endpoint must still answer with
    the last known values instead of failing the request, so the graph keeps
    rendering.
    """

    @pytest.fixture
    def app(self):
        from flask import Flask
        from routes.models import models_bp
        app = Flask(__name__)
        app.register_blueprint(models_bp)
        app.config['TESTING'] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @staticmethod
    def _snapshot(values, error=None, stale=False, age_seconds=None):
        from metrics import MetricsSnapshot
        return MetricsSnapshot(
            values=values, error=error, stale=stale, age_seconds=age_seconds
        )

    @patch('routes.models.get_models')
    def test_returns_metrics_when_the_model_answers(self, mock_get_models, client):
        mock_get_models.return_value = {'test-model': {'port': 11000, 'threads': 8}}
        snapshot = self._snapshot({
            'llamacpp:prompt_seconds_total': 10.5,
            'llamacpp:prompt_tokens_total': 1000.0,
        })

        with patch('routes.models.fetch_metrics_snapshot', return_value=snapshot):
            response = client.post(
                '/api/get-metrics',
                json={'model': 'test-model'},
                content_type='application/json',
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data['available'] is True
        assert data['stale'] is False
        assert data['metrics']['prompt_eval_ms_per_token'] == 10.5

    @patch('routes.models.get_models')
    def test_returns_last_known_metrics_when_the_model_is_busy(self, mock_get_models, client):
        mock_get_models.return_value = {'test-model': {'port': 11000}}
        snapshot = self._snapshot(
            {'llamacpp:prompt_seconds_total': 10.5},
            error='ReadTimeout from 127.0.0.1',
            stale=True,
            age_seconds=42.0,
        )

        with patch('routes.models.fetch_metrics_snapshot', return_value=snapshot):
            response = client.post(
                '/api/get-metrics',
                json={'model': 'test-model'},
                content_type='application/json',
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data['available'] is True
        assert data['stale'] is True
        assert data['cache_age_seconds'] == 42.0
        assert 'ReadTimeout' in data['warning']

    @patch('routes.models.get_models')
    def test_reports_unavailable_when_there_are_no_metrics_at_all(self, mock_get_models, client):
        mock_get_models.return_value = {'test-model': {'port': 11000}}
        snapshot = self._snapshot({}, error='ReadTimeout from 127.0.0.1', stale=True)

        with patch('routes.models.fetch_metrics_snapshot', return_value=snapshot):
            response = client.post(
                '/api/get-metrics',
                json={'model': 'test-model'},
                content_type='application/json',
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data['available'] is False
        assert data['metrics'] == {}
        assert 'ReadTimeout' in data['warning']


class TestAddModel:
    """Tests for the /api/add-model endpoint.

    The Add Model dialog was simplified to only the fields required to
    search, select and download a model, so the endpoint must accept a
    payload containing only those fields and fall back to DEFAULTS for
    every runtime-configuration field that is no longer sent.
    """

    ESSENTIAL_FIELDS = {
        "name": "Qwen3 8B Q4 K M",
        "repo": "Qwen/Qwen3-8B-GGUF",
        "filename": "Qwen3-8B-Q4_K_M.gguf",
        "expected_size": 4400000000,
        "port": 11008,
    }

    @pytest.fixture
    def app(self):
        from flask import Flask
        from routes.models import models_bp
        app = Flask(__name__)
        app.register_blueprint(models_bp)
        app.config['TESTING'] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @patch('routes.models.add_model_entry')
    def test_minimal_payload_uses_defaults_for_removed_runtime_fields(self, mock_add, client):
        """Only the essential fields are required; the rest fall back to DEFAULTS."""
        from config import DEFAULTS
        mock_add.return_value = True

        response = client.post('/api/add-model', json=self.ESSENTIAL_FIELDS,
                               content_type='application/json')

        assert response.status_code == 200
        assert response.get_json()['name'] == 'qwen3-8b-q4-k-m'

        mock_add.assert_called_once()
        kwargs = mock_add.call_args.kwargs
        # Essential fields are passed through unchanged.
        assert kwargs['repo'] == 'Qwen/Qwen3-8B-GGUF'
        assert kwargs['filename'] == 'Qwen3-8B-Q4_K_M.gguf'
        assert kwargs['expected_size'] == 4400000000
        assert kwargs['port'] == 11008
        # Runtime-configuration fields that the dialog no longer sends use DEFAULTS.
        for field in ('threads', 'ctx_size', 'gpu_layers', 'batch_size',
                      'spec_type', 'numa', 'mlock', 'flash_attn', 'cache_type_k'):
            assert kwargs[field] == DEFAULTS[field], f"{field} should default"

    @patch('routes.models.add_model_entry')
    def test_unknown_extra_fields_are_ignored(self, mock_add, client):
        """Client-only flags such as auto_download must not break the endpoint."""
        mock_add.return_value = True
        payload = dict(self.ESSENTIAL_FIELDS, auto_download=True)

        response = client.post('/api/add-model', json=payload,
                               content_type='application/json')

        assert response.status_code == 200
        mock_add.assert_called_once()

    @pytest.mark.parametrize("missing", ["name", "repo", "filename", "expected_size", "port"])
    @patch('routes.models.add_model_entry')
    def test_missing_required_field_returns_400(self, mock_add, client, missing):
        """Each essential field is still mandatory."""
        payload = {k: v for k, v in self.ESSENTIAL_FIELDS.items() if k != missing}

        response = client.post('/api/add-model', json=payload,
                               content_type='application/json')

        assert response.status_code == 400
        assert missing in response.get_json()['error']
        mock_add.assert_not_called()

    @patch('routes.models.add_model_entry')
    def test_non_integer_port_returns_400(self, mock_add, client):
        """Numeric fields must be integers."""
        payload = dict(self.ESSENTIAL_FIELDS, port='not-a-port')

        response = client.post('/api/add-model', json=payload,
                               content_type='application/json')

        assert response.status_code == 400
        mock_add.assert_not_called()

    @patch('routes.models.add_model_entry')
    def test_duplicate_model_returns_409(self, mock_add, client):
        """A rejected add (e.g. duplicate name) surfaces as 409."""
        mock_add.return_value = False

        response = client.post('/api/add-model', json=self.ESSENTIAL_FIELDS,
                               content_type='application/json')

        assert response.status_code == 409
        assert 'error' in response.get_json()


class TestSystemRoutes:
    """Tests for system routes (optimize-settings, benchmark-optimize)."""
    
    @pytest.fixture
    def make_app(self):
        """Factory to create app after patches are applied."""
        def _make_app():
            from flask import Flask
            from routes.system import system_bp
            app = Flask(__name__)
            app.register_blueprint(system_bp)
            app.config['TESTING'] = True
            return app
        return _make_app
    
    @pytest.fixture
    def client(self, make_app):
        return make_app().test_client()
    
    @patch('routes.system.get_models')
    def test_optimize_settings_missing_model(self, mock_get_models, make_app):
        """Test optimize-settings without model name returns 400."""
        mock_get_models.return_value = {}
        
        client = make_app().test_client()
        response = client.post('/api/optimize-settings',
            json={},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data['error'] == 'Missing model name'
    
    @patch('routes.system.get_models')
    def test_optimize_settings_unknown_model(self, mock_get_models, make_app):
        """Test optimize-settings with unknown model returns 404."""
        mock_get_models.return_value = {'known-model': {'model_path': 'models/known.gguf'}}
        
        client = make_app().test_client()
        response = client.post('/api/optimize-settings',
            json={'model': 'unknown-model'},
            content_type='application/json')
        
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert "Unknown model 'unknown-model'" in data['error']
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    def test_optimize_settings_success(self, mock_hardware_info, mock_get_models, make_app):
        """Test optimize-settings returns optimal settings."""
        mock_get_models.return_value = {
            'test-model': {
                'model_path': 'models/test.gguf',
                'file_size_gb': 4.5,
                'ctx_size': 4096,
            }
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
            'has_amd_gpu': False,
            'has_intel_gpu': False,
            'gpu_info': [{'name': 'RTX 3060', 'vendor': 'NVIDIA', 'memory_mb': 8192}],
        }
        
        client = make_app().test_client()
        response = client.post('/api/optimize-settings',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'optimal_settings' in data
        assert 'hardware' in data
        assert data['model'] == 'test-model'
        assert 'gpu_layers' in data['optimal_settings']
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_missing_model(self, mock_optimizer_class, mock_resolve_path, 
                                               mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize without model name returns 400."""
        mock_get_models.return_value = {}
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data['error'] == 'Missing model name'
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_unknown_model(self, mock_optimizer_class, mock_resolve_path, 
                                               mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize with unknown model returns 404."""
        mock_get_models.return_value = {'known-model': {'model_path': 'models/known.gguf'}}
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'unknown-model'},
            content_type='application/json')
        
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert "Unknown model 'unknown-model'" in data['error']
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_invalid_target(self, mock_optimizer_class, mock_resolve_path,
                                                mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize with invalid target returns 400."""
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'target': 'invalid'},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert "Invalid target 'invalid'" in data['error']
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_invalid_duration(self, mock_optimizer_class, mock_resolve_path,
                                                  mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize with invalid max_duration_sec returns 400."""
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        
        client = make_app().test_client()
        
        # Test too low (negative)
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': -1},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'max_duration_sec must be an integer between 0 and 3600' in data['error']
        
        # Test too high
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': 4000},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'max_duration_sec must be an integer between 0 and 3600' in data['error']
        
        # Test 0 is valid (no timeout)
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': 0},
            content_type='application/json')
        
        # Should not return 400 (may return 500 due to mock but not 400 for validation)
        assert response.status_code != 400
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_no_model_path(self, mock_optimizer_class, mock_resolve_path,
                                               mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize with model missing file path returns 400."""
        mock_get_models.return_value = {
            'test-model': {'model_path': ''}  # Empty path
        }
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert "has no associated file path" in data['error']
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_model_file_not_found(self, mock_optimizer_class, mock_resolve_path,
                                                      mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize when model file doesn't exist returns 404."""
        from pathlib import Path
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_resolve_path.return_value = Path('/nonexistent/test.gguf')
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert 'Model file not found' in data['error']
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_success(self, mock_optimizer_class, mock_resolve_path,
                                         mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize returns optimization results."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        # Setup mocks
        mock_get_models.return_value = {
            'test-model': {
                'model_path': 'models/test.gguf',
                'file_size_gb': 4.5,
                'ctx_size': 4096,
            }
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
            'has_amd_gpu': False,
            'has_intel_gpu': False,
            'gpu_info': [{'name': 'RTX 3060', 'vendor': 'NVIDIA', 'memory_mb': 8192}],
        }
        # Create a mock path object with exists() returning True
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        # Mock optimizer
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = None  # Cache miss
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {
                'n_gpu_layers': 50,
                'batch_size': 2048,
                'threads': 8,
                'threads_batch': 8,
                'cache_type_k': 'q4_0',
                'cache_type_v': 'q4_0',
                'flash_attn': 'auto',
                'ctx_size': 4096,
            },
            'benchmark_results': [
                {'config': {'n_gpu_layers': 50}, 'success': True, 'pp_avg': 1000.0, 'tg_avg': 50.0}
            ],
            'hardware_info': mock_hardware_info.return_value,
            'model_info': {'file_size_gb': 4.5},
            'cache_key': 'test_cache_key',
            'optimization_target': 'balanced',
            'new_benchmarks_run': True,
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'target': 'balanced', 'max_duration_sec': 60},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Check response structure
        assert 'optimal_settings' in data
        assert 'benchmark_results' in data
        assert 'hardware_info' in data
        assert 'cached' in data
        assert 'duration_sec' in data
        assert 'optimization_target' in data
        assert 'model' in data
        
        # Check optimal_settings format (gpu_layers instead of n_gpu_layers for compatibility)
        assert 'gpu_layers' in data['optimal_settings']
        assert data['optimal_settings']['gpu_layers'] == 50
        assert 'n_gpu_layers' not in data['optimal_settings']
        
        # Check cached is False (cache miss)
        assert data['cached'] is False
        
        # Check optimization target
        assert data['optimization_target'] == 'balanced'
        assert data['model'] == 'test-model'
        
        # Verify optimizer was called with correct parameters
        mock_optimizer.optimize.assert_called_once_with(
            str(mock_path),
            max_duration_sec=60,
            optimization_target='balanced'
        )
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_cached(self, mock_optimizer_class, mock_resolve_path,
                                        mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize returns cached result when available."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {
                'model_path': 'models/test.gguf',
                'file_size_gb': 4.5,
                'ctx_size': 4096,
            }
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        # Create a mock path object with exists() returning True
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        # Return cached result
        mock_optimizer._load_from_cache.return_value = {
            'optimal_settings': {'n_gpu_layers': 30, 'batch_size': 1024},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
        }
        # optimize() now handles caching internally - return cached result with new_benchmarks_run=False
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 30, 'batch_size': 1024},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'balanced',
            'new_benchmarks_run': False,
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Should use cached result
        assert data['cached'] is True
        assert data['optimal_settings']['gpu_layers'] == 30
    
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_fallback_reason(self, mock_optimizer_class, mock_resolve_path,
                                                 mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize includes fallback_reason when optimizer falls back."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        # Create a mock path object with exists() returning True
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = None
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 0, 'batch_size': 512},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'balanced',
            'fallback_reason': 'BinaryNotFoundError: llama-bench not found',
            'new_benchmarks_run': True,
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert 'fallback_reason' in data
        assert 'llama-bench not found' in data['fallback_reason']

    def test_benchmark_optimize_invalid_model_type(self, make_app):
        """Test benchmark-optimize with non-string model coerces to string and returns 404 for unknown model."""
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 123},
            content_type='application/json')
        
        # Non-string model is coerced to string "123", then returns 404 (unknown model)
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert "Unknown model '123'" in data['error']

    def test_benchmark_optimize_invalid_target_type(self, make_app):
        """Test benchmark-optimize with non-string target coerces to string and returns 400 for invalid target."""
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'target': 5},
            content_type='application/json')
        
        # Non-string target is coerced to string "5", then returns 400 for invalid target value
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert "Invalid target '5'" in data['error']

    def test_benchmark_optimize_boolean_duration(self, make_app):
        """Test benchmark-optimize with boolean max_duration_sec returns 400."""
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': True},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'must be an integer' in data['error']

    def test_optimize_settings_invalid_model_type(self, make_app):
        """Test optimize-settings with non-string model coerces to string and returns 404 for unknown model."""
        client = make_app().test_client()
        response = client.post('/api/optimize-settings',
            json={'model': 123},
            content_type='application/json')
        
        # Non-string model is coerced to string "123", then returns 404 (unknown model)
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert "Unknown model '123'" in data['error']

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_cached_with_fallback_reason(self, mock_optimizer_class, mock_resolve_path,
                                                             mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize returns cached result with fallback_reason."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {
                'model_path': 'models/test.gguf',
                'file_size_gb': 4.5,
                'ctx_size': 4096,
            }
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = {
            'optimal_settings': {'n_gpu_layers': 30, 'batch_size': 1024},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'fallback_reason': 'BinaryNotFoundError: llama-bench not found',
        }
        # optimize() now handles caching internally
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 30, 'batch_size': 1024},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'balanced',
            'fallback_reason': 'BinaryNotFoundError: llama-bench not found',
            'new_benchmarks_run': False,
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Should use cached result
        assert data['cached'] is True
        assert data['optimal_settings']['gpu_layers'] == 30
        assert 'fallback_reason' in data
        assert 'llama-bench not found' in data['fallback_reason']

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_throughput_target(self, mock_optimizer_class, mock_resolve_path,
                                                   mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize with throughput target."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {
                'model_path': 'models/test.gguf',
                'file_size_gb': 4.5,
                'ctx_size': 4096,
            }
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = None
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 50, 'batch_size': 2048},
            'benchmark_results': [{'config': {}, 'success': True}],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'throughput',
            'new_benchmarks_run': True,
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'target': 'throughput', 'max_duration_sec': 60},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['optimization_target'] == 'throughput'
        mock_optimizer.optimize.assert_called_once_with(
            str(mock_path), max_duration_sec=60, optimization_target='throughput'
        )

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_latency_target(self, mock_optimizer_class, mock_resolve_path,
                                                mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize with latency target."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8, 'cpu_count_physical': 4,
            'memory_total_gb': 16, 'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = None
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 20, 'batch_size': 1024},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'latency',
            'new_benchmarks_run': True,
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'target': 'latency', 'max_duration_sec': 30},
            content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['optimization_target'] == 'latency'

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_max_duration_boundary(self, mock_optimizer_class, mock_resolve_path,
                                                       mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize with min/max duration values."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8, 'cpu_count_physical': 4,
            'memory_total_gb': 16, 'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = None
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 50},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'balanced',
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        
        # Test min duration (10)
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': 10},
            content_type='application/json')
        assert response.status_code == 200
        mock_optimizer.optimize.assert_called_with(str(mock_path), max_duration_sec=10, optimization_target='balanced')
        
        # Test max duration (3600)
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': 3600},
            content_type='application/json')
        assert response.status_code == 200
        mock_optimizer.optimize.assert_called_with(str(mock_path), max_duration_sec=3600, optimization_target='balanced')

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_optimize_optimizer_exception(self, mock_optimizer_class, mock_resolve_path,
                                                     mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize handles optimizer exceptions."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8, 'cpu_count_physical': 4,
            'memory_total_gb': 16, 'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = None
        mock_optimizer.optimize.side_effect = Exception("Optimization failed")
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 500
        data = response.get_json()
        assert 'error' in data
        assert 'Optimization failed' in data['error']

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    def test_benchmark_optimize_file_not_found(self, mock_resolve_path, mock_hardware_info, mock_get_models, make_app):
        """Test benchmark-optimize returns 404 when model file not found."""
        from unittest.mock import MagicMock
        from pathlib import Path
        
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8, 'cpu_count_physical': 4,
            'memory_total_gb': 16, 'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False  # File doesn't exist
        mock_resolve_path.return_value = mock_path
        
        client = make_app().test_client()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'},
            content_type='application/json')
        
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert 'Model file not found' in data['error']

    @patch('routes.system.get_hardware_info')
    def test_api_hardware_info(self, mock_hardware_info, make_app):
        """Test /api/hardware-info endpoint."""
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
            'has_amd_gpu': False,
            'has_intel_gpu': False,
            'gpu_info': [{'name': 'RTX 3060', 'vendor': 'NVIDIA', 'memory_mb': 8192}],
        }
        
        client = make_app().test_client()
        response = client.get('/api/hardware-info')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['cpu_count_logical'] == 8
        assert data['total_gpu_vram_gb'] == 8
        assert data['has_nvidia_gpu'] is True

    @patch('routes.system.get_hardware_info')
    def test_api_hardware_info_exception(self, mock_hardware_info, make_app):
        """Test /api/hardware-info handles exceptions."""
        mock_hardware_info.side_effect = Exception("Hardware detection failed")
        
        client = make_app().test_client()
        response = client.get('/api/hardware-info')
        
        assert response.status_code == 500
        data = response.get_json()
        assert 'error' in data

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    def test_optimize_settings_with_various_models(self, mock_hardware_info, mock_get_models, make_app):
        """Test optimize-settings with different model configurations."""
        mock_get_models.return_value = {
            'small-model': {'model_path': 'models/small.gguf', 'file_size_gb': 1.5, 'ctx_size': 2048},
            'large-model': {'model_path': 'models/large.gguf', 'file_size_gb': 20.0, 'ctx_size': 32768},
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 16,
            'cpu_count_physical': 8,
            'memory_total_gb': 64,
            'total_gpu_vram_gb': 24,
            'has_nvidia_gpu': True,
            'has_amd_gpu': False,
            'has_intel_gpu': False,
            'gpu_info': [{'name': 'RTX 4090', 'vendor': 'NVIDIA', 'memory_mb': 24576}],
        }
        
        client = make_app().test_client()
        
        # Test small model
        response = client.post('/api/optimize-settings',
            json={'model': 'small-model'}, content_type='application/json')
        assert response.status_code == 200
        data = response.get_json()
        assert data['model'] == 'small-model'
        assert 'optimal_settings' in data
        
        # Test large model
        response = client.post('/api/optimize-settings',
            json={'model': 'large-model'}, content_type='application/json')
        assert response.status_code == 200
        data = response.get_json()
        assert data['model'] == 'large-model'
        assert 'optimal_settings' in data
        # Large model on high VRAM should have high gpu_layers
        assert data['optimal_settings'].get('gpu_layers', 0) >= 50

    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    def test_optimize_settings_cpu_only(self, mock_hardware_info, mock_get_models, make_app):
        """Test optimize-settings with CPU-only hardware."""
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf', 'file_size_gb': 4.5, 'ctx_size': 4096},
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8,
            'cpu_count_physical': 4,
            'memory_total_gb': 16,
            'total_gpu_vram_gb': 0,
            'has_nvidia_gpu': False,
            'has_amd_gpu': False,
            'has_intel_gpu': False,
            'gpu_info': [],
        }
        
        client = make_app().test_client()
        response = client.post('/api/optimize-settings',
            json={'model': 'test-model'}, content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['optimal_settings']['gpu_layers'] == 0
        assert data['optimal_settings']['flash_attn'] == 'off'


class TestBenchmarkOptimizationPerformance:
    """Performance tests for benchmark optimization (run with RUN_PERF_TESTS=1)."""
    
    @pytest.mark.skipif(
        not os.environ.get('RUN_PERF_TESTS'),
        reason="Performance tests require RUN_PERF_TESTS=1 and test model/binaries"
    )
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_benchmark_sweep_time_budget(self, mock_optimizer_class, mock_resolve_path,
                                          mock_hardware_info, mock_get_models, make_app):
        """Verify benchmark sweep completes within time budget."""
        from unittest.mock import MagicMock
        from pathlib import Path
        import time
        
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8, 'cpu_count_physical': 4,
            'memory_total_gb': 16, 'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = None
        
        # Simulate a sweep that takes some time
        def slow_optimize(*args, **kwargs):
            time.sleep(0.1)  # Simulate work
            return {
                'optimal_settings': {'n_gpu_layers': 50},
                'benchmark_results': [{'config': {}, 'success': True}],
                'hardware_info': mock_hardware_info.return_value,
                'cache_key': 'test_cache_key',
                'optimization_target': 'balanced',
            }
        mock_optimizer.optimize.side_effect = slow_optimize
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        start = time.time()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': 5},
            content_type='application/json')
        elapsed = time.time() - start
        
        assert response.status_code == 200
        data = response.get_json()
        # Should complete within max_duration_sec + some overhead
        assert elapsed < 10  # Generous upper bound
        assert data['duration_sec'] < 10

    @pytest.mark.skipif(
        not os.environ.get('RUN_PERF_TESTS'),
        reason="Performance tests require RUN_PERF_TESTS=1 and test model/binaries"
    )
    @patch('routes.system.get_models')
    @patch('routes.system.get_hardware_info')
    @patch('config.resolve_model_path')
    @patch('optimizer.BenchmarkOptimizer')
    def test_cache_hit_performance(self, mock_optimizer_class, mock_resolve_path,
                                    mock_hardware_info, mock_get_models, make_app):
        """Verify cache hit reduces response time to < 1s."""
        from unittest.mock import MagicMock
        from pathlib import Path
        import time
        
        mock_get_models.return_value = {
            'test-model': {'model_path': 'models/test.gguf'}
        }
        mock_hardware_info.return_value = {
            'cpu_count_logical': 8, 'cpu_count_physical': 4,
            'memory_total_gb': 16, 'total_gpu_vram_gb': 8,
            'has_nvidia_gpu': True,
        }
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve_path.return_value = mock_path
        
        mock_optimizer = Mock()
        mock_optimizer._generate_cache_key.return_value = 'test_cache_key'
        mock_optimizer._load_from_cache.return_value = {
            'optimal_settings': {'n_gpu_layers': 30},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
        }
        mock_optimizer_class.return_value = mock_optimizer
        
        client = make_app().test_client()
        
        # First request - cache miss (new benchmarks run)
        mock_optimizer._load_from_cache.return_value = None
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 50},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'balanced',
            'new_benchmarks_run': True,
        }
        start = time.time()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'}, content_type='application/json')
        first_elapsed = time.time() - start
        
        assert response.status_code == 200
        assert response.get_json()['cached'] is False
        
        # Second request - cache hit (no new benchmarks)
        mock_optimizer._load_from_cache.return_value = {
            'optimal_settings': {'n_gpu_layers': 30},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
        }
        mock_optimizer.optimize.return_value = {
            'optimal_settings': {'n_gpu_layers': 30},
            'benchmark_results': [],
            'hardware_info': mock_hardware_info.return_value,
            'cache_key': 'test_cache_key',
            'optimization_target': 'balanced',
            'new_benchmarks_run': False,
        }
        start = time.time()
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model'}, content_type='application/json')
        second_elapsed = time.time() - start
        
        assert response.status_code == 200
        assert response.get_json()['cached'] is True
        # Cache hit should be much faster
        assert second_elapsed < 1.0  # Less than 1 second
        assert second_elapsed < first_elapsed * 0.5  # At least 2x faster


if __name__ == "__main__":
    unittest.main()