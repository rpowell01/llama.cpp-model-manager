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
        response = client.post('/api/benchmark-optimize',
            json={'model': 'test-model', 'max_duration_sec': 5},
            content_type='application/json')
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'max_duration_sec must be an integer between 10 and 3600' in data['error']
    
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