"""
Tests for mnemonic-proxy integration.
"""
import pytest
import json
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Import the module under test
from mnemonic_proxy import MnemonicProxyManager


class TestMnemonicProxyConfigGeneration:
    """Test mnemonic-proxy config generation from model settings."""
    
    @pytest.fixture
    def mock_model_config(self):
        """Mock model configuration."""
        return {
            "ctx_size": 131072,
            "slot_save_path": "/tmp/slots",
            "spec_type": "ngram-mod",
            "model_path": "/tmp/model.gguf",
        }
    
    def test_generate_config_basic(self, mock_model_config):
        """Test basic config generation."""
        with patch('mnemonic_proxy.get_models', return_value={'test-model': mock_model_config}):
            manager = MnemonicProxyManager('test-model', 9931)
            config = manager.generate_config()
            
            assert config['engine'] == 'llama.cpp'
            # v0.3.24+: window is auto-discovered from engine, not in config
            assert 'window' not in config
            assert config['upstream'] == 'http://127.0.0.1:9931'
            assert config['slot_dir'] == '/tmp/slots'
            assert config['mask_anchor'] is True
            assert config['autosave'] is True
            assert config['window_estimate_slack'] == 0.15
    
    def test_generate_config_min_window(self):
        """Test that window minimum is not in config (auto-discovered)."""
        mock_config = {
            "ctx_size": 8192,  # Below minimum
            "slot_save_path": "",
            "spec_type": "none",
            "model_path": "/tmp/model.gguf",
        }
        with patch('mnemonic_proxy.get_models', return_value={'test-model': mock_config}):
            manager = MnemonicProxyManager('test-model', 9931)
            config = manager.generate_config()
            
            # v0.3.24+: window is auto-discovered from engine, not in config
            assert 'window' not in config
            assert config['engine'] == 'llama.cpp'
    
    def test_generate_config_speculative_engine(self):
        """Test engine detection for speculative decoding."""
        mock_config = {
            "ctx_size": 32768,
            "slot_save_path": "",
            "spec_type": "draft-mtp,ngram-mod",
            "model_path": "/tmp/model.gguf",
        }
        with patch('mnemonic_proxy.get_models', return_value={'test-model': mock_config}):
            manager = MnemonicProxyManager('test-model', 9931)
            config = manager.generate_config()
            
            assert config['engine'] == 'llama.cpp'
    
    def test_generate_config_dflash_engine(self):
        """Test engine detection for draft-dflash."""
        mock_config = {
            "ctx_size": 32768,
            "slot_save_path": "",
            "spec_type": "draft-dflash",
            "model_path": "/tmp/model.gguf",
        }
        with patch('mnemonic_proxy.get_models', return_value={'test-model': mock_config}):
            manager = MnemonicProxyManager('test-model', 9931)
            config = manager.generate_config()
            
            assert config['engine'] == 'llama.cpp'
    
    def test_generate_config_no_model_config(self):
        """Test config generation when model config not found."""
        with patch('mnemonic_proxy.get_models', return_value={}):
            manager = MnemonicProxyManager('unknown-model', 9931)
            config = manager.generate_config()
            
            assert config['engine'] == 'llama.cpp'
            # v0.3.24+: window is auto-discovered from engine, not in config
            assert 'window' not in config
            assert config['upstream'] == 'http://127.0.0.1:9931'
    
    def test_generate_config_with_mcp(self):
        """Test config generation with MCP enabled."""
        mock_config = {
            "ctx_size": 32768,
            "slot_save_path": "",
            "spec_type": "none",
            "model_path": "/tmp/model.gguf",
        }
        with patch('mnemonic_proxy.get_models', return_value={'test-model': mock_config}):
            manager = MnemonicProxyManager('test-model', 9931, mcp_enabled=True, mcp_port=8133)
            config = manager.generate_config()
            
            assert config['mcp_port'] == 8133
    
    def test_generate_config_custom_override(self):
        """Test config generation with custom override."""
        mock_config = {
            "ctx_size": 32768,
            "slot_save_path": "",
            "spec_type": "none",
            "model_path": "/tmp/model.gguf",
        }
        custom_override = {
            "mask_anchor": False,
            "custom_setting": "value"
        }
        with patch('mnemonic_proxy.get_models', return_value={'test-model': mock_config}):
            manager = MnemonicProxyManager('test-model', 9931, config_override=custom_override)
            config = manager.generate_config()
            
            assert config['mask_anchor'] is False
            assert config['custom_setting'] == 'value'


class TestMnemonicProxyManager:
    """Test MnemonicProxyManager class."""
    
    def test_init_creates_data_dir(self, tmp_path):
        """Test that initialization creates data directory."""
        data_dir = str(tmp_path / "test-data")
        
        with patch('mnemonic_proxy.get_models', return_value={}):
            with patch('mnemonic_proxy._validate_local_mode'):
                manager = MnemonicProxyManager(
                    'test-model', 9931, data_dir=data_dir
                )
        
        assert Path(data_dir).exists()
    
    def test_init_sets_defaults(self):
        """Test that initialization sets correct defaults."""
        with patch('mnemonic_proxy.get_models', return_value={}):
            with patch('mnemonic_proxy._validate_local_mode'):
                manager = MnemonicProxyManager('test-model', 9931)
        
        assert manager.model_name == 'test-model'
        assert manager.llama_port == 9931
        assert manager.proxy_port == 8096
        assert manager.mcp_enabled is False
        assert manager.mcp_port == 8133
        assert manager.config_override['mask_anchor'] is True
    
    def test_get_mcp_status_not_running(self):
        """Test MCP status when not running."""
        with patch('mnemonic_proxy.get_models', return_value={}):
            with patch('mnemonic_proxy._validate_local_mode'):
                manager = MnemonicProxyManager('test-model', 9931, mcp_enabled=True)
        
        status = manager.get_mcp_status()
        
        assert status['enabled'] is True
        assert status['running'] is False
        assert status['port'] == 8133
        assert status['pid'] is None
        assert status['uptime_seconds'] == 0
    
    def test_get_status_includes_mcp(self):
        """Test that get_status includes MCP status."""
        with patch('mnemonic_proxy.get_models', return_value={}):
            with patch('mnemonic_proxy._validate_local_mode'):
                manager = MnemonicProxyManager('test-model', 9931, mcp_enabled=True)
        
        with patch.object(manager, 'is_running', return_value=False):
            status = manager.get_status()
        
        assert 'mcp' in status
        assert status['mcp']['enabled'] is True
        assert status['mcp']['running'] is False


class TestMnemonicProxyAPIEndpoints:
    """Test mnemonic-proxy API endpoints (integration tests)."""
    
    def test_proxy_endpoint_functions_exist(self):
        """Test that proxy endpoint functions exist in lifecycle module."""
        import routes.lifecycle as lifecycle
        
        # Check that all endpoint functions exist
        assert hasattr(lifecycle, 'api_proxy_status')
        assert hasattr(lifecycle, 'api_proxy_start')
        assert hasattr(lifecycle, 'api_proxy_stop')
        assert hasattr(lifecycle, 'api_proxy_logs')
        assert hasattr(lifecycle, 'api_proxy_list')
        assert hasattr(lifecycle, 'api_proxy_mcp_start')
        assert hasattr(lifecycle, 'api_proxy_mcp_stop')
        assert hasattr(lifecycle, 'api_proxy_mcp_status')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])