"""
Tests for mnemonic-proxy integration.
"""
import pytest
import json
import os
import subprocess
import sys
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


class TestMnemonicProxyStartReadiness:
    """Regression tests for the 'failed to start' false negative.

    The proxy's /health endpoint reports the *upstream engine's* health too, so
    a slow/busy llama-server keeps it non-200 for a while. start() must poll
    patiently instead of giving up after a single 3s-delayed 2s-timeout check,
    and must never leave the process orphaned holding the port.
    """

    def _make_manager(self, tmp_path):
        with patch('mnemonic_proxy.get_models', return_value={}):
            with patch('mnemonic_proxy._validate_local_mode'):
                return MnemonicProxyManager(
                    'test-model', 9931, data_dir=str(tmp_path / "data")
                )

    def test_health_state_ok(self, tmp_path):
        manager = self._make_manager(tmp_path)
        manager._pid = 1234
        with patch('mnemonic_proxy.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=200)
            assert manager._health_state() == "ok"
        assert manager._last_health == "ok"

    def test_health_state_listening_on_503(self, tmp_path):
        """503 = engine still loading: the proxy itself is up."""
        manager = self._make_manager(tmp_path)
        manager._pid = 1234
        with patch('mnemonic_proxy.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=503)
            assert manager._health_state() == "listening"

    def test_health_state_listening_on_timeout(self, tmp_path):
        """Read timeout = connected but slow handler (busy upstream)."""
        import requests as _requests
        manager = self._make_manager(tmp_path)
        manager._pid = 1234
        with patch('mnemonic_proxy.requests.get',
                   side_effect=_requests.exceptions.Timeout()):
            assert manager._health_state() == "listening"

    def test_health_state_down_on_connection_error(self, tmp_path):
        import requests as _requests
        manager = self._make_manager(tmp_path)
        manager._pid = 1234
        with patch('mnemonic_proxy.requests.get',
                   side_effect=_requests.exceptions.ConnectionError()):
            assert manager._health_state() == "down"

    def test_start_waits_for_slow_proxy(self, tmp_path):
        """Health only answers 200 after several polls -> start() succeeds."""
        manager = self._make_manager(tmp_path)
        states = iter(["down", "down", "listening", "listening", "ok"])

        with patch.object(manager, 'build_cmd', return_value=['fake-proxy']), \
             patch('mnemonic_proxy.subprocess.Popen') as mock_popen, \
             patch.object(manager, '_health_state', side_effect=lambda **kw: next(states)), \
             patch.object(manager, '_kill_process') as mock_kill, \
             patch('mnemonic_proxy.PROXY_START_POLL_INTERVAL', 0.01):
            mock_popen.return_value = Mock(pid=4242, poll=Mock(return_value=None))
            assert manager.start() is True

        # stdout/stderr must be captured to a file, not DEVNULL
        _, kwargs = mock_popen.call_args
        assert kwargs['stdout'] is not subprocess.DEVNULL
        assert kwargs['stderr'] is subprocess.STDOUT
        # process must not be killed on the success path
        mock_kill.assert_not_called()

    def test_start_fails_fast_when_process_exits(self, tmp_path):
        """A proxy that dies during startup fails immediately (no 30s wait)."""
        manager = self._make_manager(tmp_path)
        with patch.object(manager, 'build_cmd', return_value=['fake-proxy']), \
             patch('mnemonic_proxy.subprocess.Popen') as mock_popen, \
             patch.object(manager, '_kill_process') as mock_kill, \
             patch.object(manager, '_health_state', return_value="down"):
            mock_popen.return_value = Mock(pid=4242, poll=Mock(return_value=1))
            assert manager.start(timeout=30) is False
        mock_kill.assert_called_once_with(4242)
        assert manager._pid is None

    def test_start_kills_process_on_timeout(self, tmp_path):
        """Never leave an orphaned proxy holding the port after a failure."""
        manager = self._make_manager(tmp_path)
        with patch.object(manager, 'build_cmd', return_value=['fake-proxy']), \
             patch('mnemonic_proxy.subprocess.Popen') as mock_popen, \
             patch.object(manager, '_health_state', return_value="down"), \
             patch.object(manager, '_kill_process') as mock_kill, \
             patch('mnemonic_proxy.PROXY_START_POLL_INTERVAL', 0.01):
            mock_popen.return_value = Mock(pid=4242, poll=Mock(return_value=None))
            assert manager.start(timeout=0.05) is False
        mock_kill.assert_called_once_with(4242)
        assert manager._pid is None
        assert manager._process is None

    def test_start_does_not_block_stop(self, tmp_path):
        """The readiness poll must run outside self._lock."""
        manager = self._make_manager(tmp_path)
        observed = {}

        def health(**kw):
            # record whether the lock is held while polling for readiness
            observed['locked'] = manager._lock.locked()
            return "down"

        with patch.object(manager, 'build_cmd', return_value=['fake-proxy']), \
             patch('mnemonic_proxy.subprocess.Popen') as mock_popen, \
             patch.object(manager, '_health_state', side_effect=health), \
             patch('mnemonic_proxy.PROXY_START_POLL_INTERVAL', 0.01):
            mock_popen.return_value = Mock(pid=4242, poll=Mock(return_value=None))
            manager.start(timeout=0.2)

        assert observed.get('locked') is False

    def test_get_logs_reads_log_file(self, tmp_path):
        manager = self._make_manager(tmp_path)
        manager.log_path.write_text("line1\nline2\nline3\n")
        assert manager.get_logs(lines=2) == ["line2", "line3"]

    def test_get_logs_missing_file(self, tmp_path):
        manager = self._make_manager(tmp_path)
        assert manager.get_logs() == []


class TestMnemonicProxyConfigPrecedence:
    """config_override must not clobber per-model derived values."""

    def test_stale_override_slot_dir_and_upstream_ignored(self):
        """models.json had qwen's slot_dir pointing at mellum2's data dir."""
        mock_config = {
            "slot_save_path": "./data/qwen3-30b-a3b-instruct-2507-q4-k-s",
            "model_path": "/tmp/model.gguf",
        }
        stale_override = {
            "slot_dir": "data/mellum2-12b-a2.5b-instruct.i1-q4-k-m",
            "upstream": "http://127.0.0.1:9931",
            "mask_anchor": False,
        }
        with patch('mnemonic_proxy.get_models',
                   return_value={'qwen': mock_config}):
            manager = MnemonicProxyManager(
                'qwen', 8123, config_override=stale_override
            )
            config = manager.generate_config()

        # derived from the live model config, not the stale override
        assert config['slot_dir'] == 'data/qwen3-30b-a3b-instruct-2507-q4-k-s'
        assert config['upstream'] == 'http://127.0.0.1:8123'
        # unrelated override keys still apply
        assert config['mask_anchor'] is False

    def test_override_slot_dir_used_when_model_has_none(self):
        mock_config = {"slot_save_path": "", "model_path": "/tmp/model.gguf"}
        with patch('mnemonic_proxy.get_models', return_value={'m': mock_config}):
            manager = MnemonicProxyManager(
                'm', 9931, config_override={"slot_dir": "data/fallback"}
            )
            config = manager.generate_config()
        assert config['slot_dir'] == 'data/fallback'


class TestCreateProxyManager:
    """Stale (not running) managers must be replaced so new config applies."""

    def test_stale_manager_is_replaced(self):
        import mnemonic_proxy as mp
        mp._proxy_managers.clear()
        try:
            with patch('mnemonic_proxy._validate_local_mode'):
                m1 = mp.create_proxy_manager('model-a', 9931, data_dir='/tmp/a')
                assert m1.llama_port == 9931
                # same model, new port: stale (not running) manager must be replaced
                m2 = mp.create_proxy_manager('model-a', 9955, data_dir='/tmp/a2')
                assert m2 is not m1
                assert m2.llama_port == 9955
        finally:
            mp._proxy_managers.clear()

    def test_running_manager_is_kept(self):
        import mnemonic_proxy as mp
        mp._proxy_managers.clear()
        try:
            with patch('mnemonic_proxy._validate_local_mode'):
                m1 = mp.create_proxy_manager('model-b', 9931, data_dir='/tmp/b')
                m1._pid = 999  # pretend it is running
                m2 = mp.create_proxy_manager('model-b', 9955, data_dir='/tmp/b')
                assert m2 is m1
                assert m2.llama_port == 9931
        finally:
            mp._proxy_managers.clear()


class TestMnemonicProxyZstdSupport:
    """kv_archive needs stdlib compression.zstd (Python >= 3.14).

    On older interpreters the shim must be preloaded *inside the proxy process*
    (the web app importing it does nothing for the child), otherwise the proxy
    exits immediately with a SystemExit - the original bug.
    """

    KV_OVERRIDE = {"kv_archive": True, "kv_archive_level": 3}

    def _manager(self, tmp_path, config_override=None):
        with patch('mnemonic_proxy.get_models', return_value={
                'm': {'slot_save_path': '', 'model_path': '/tmp/model.gguf'}}):
            with patch('mnemonic_proxy._validate_local_mode'):
                return MnemonicProxyManager(
                    'm', 9931, data_dir=str(tmp_path / "data"),
                    config_override=config_override if config_override is not None
                    else dict(self.KV_OVERRIDE),
                )

    def test_needs_zstd_shim_detects_missing_module(self):
        """On Python < 3.14 without compression.zstd the shim is required."""
        if sys.version_info >= (3, 14):
            pytest.skip("running on Python >= 3.14 (native compression.zstd)")
        import mnemonic_proxy as mp
        with patch.dict(sys.modules):
            sys.modules.pop('compression.zstd', None)
            sys.modules.pop('compression', None)
            assert mp._needs_zstd_shim() is True

    def test_zstd_shim_usable_true_when_installed(self):
        import mnemonic_proxy as mp
        with patch.object(mp, '_needs_zstd_shim', return_value=True):
            assert mp._zstd_shim_usable() is True  # shim file + zstandard present

    def test_zstd_shim_usable_false_without_zstandard(self):
        import mnemonic_proxy as mp
        with patch.object(mp, '_needs_zstd_shim', return_value=True), \
             patch.dict(sys.modules, {'zstandard': None}):
            assert mp._zstd_shim_usable() is False

    def test_apply_runtime_requirements_disables_kv_archive_without_shim(self, tmp_path):
        """Without the shim the proxy would exit: turn kv_archive off instead."""
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path)
        with patch.object(mp, '_zstd_shim_usable', return_value=False):
            config = manager._apply_runtime_requirements({"kv_archive": True})
        assert config["kv_archive"] is False

    def test_apply_runtime_requirements_keeps_kv_archive_with_shim(self, tmp_path):
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path)
        with patch.object(mp, '_zstd_shim_usable', return_value=True):
            config = manager._apply_runtime_requirements({"kv_archive": True})
        assert config["kv_archive"] is True

    def test_apply_runtime_requirements_ignores_absent_kv_archive(self, tmp_path):
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path)
        with patch.object(mp, '_zstd_shim_usable', return_value=False):
            config = manager._apply_runtime_requirements({"autosave": True})
        assert "kv_archive" not in config
        assert config["autosave"] is True

    def test_launcher_prefix_empty_without_kv_archive(self, tmp_path):
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path, config_override={"autosave": True})
        with patch.object(mp, '_needs_zstd_shim', return_value=True):
            assert manager._launcher_prefix(Path("/fake/binary"),
                                            {"autosave": True}) == []

    def test_launcher_prefix_empty_on_python_314(self, tmp_path):
        """Python >= 3.14 has compression.zstd: no preloader needed."""
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path)
        with patch.object(mp, '_needs_zstd_shim', return_value=False):
            assert manager._launcher_prefix(
                Path("/fake/binary"), {"kv_archive": True}) == []

    def test_launcher_prefix_preloads_shim(self, tmp_path):
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path)
        with patch.object(mp, '_needs_zstd_shim', return_value=True):
            cmd = manager._launcher_prefix(Path("/fake/mnemonic-proxy"),
                                           {"kv_archive": True})
        assert len(cmd) == 3
        assert cmd[1] == "-c"
        assert "import compression_zstd_shim" in cmd[2]
        assert "ctxproxy.server import main" in cmd[2]
        # the script path is embedded for argv[0], never passed as an argument
        assert "/fake/mnemonic-proxy" in cmd[2]

    def test_build_cmd_uses_preloader_for_kv_archive(self, tmp_path):
        """Regression: the binary path must not become a stray argv entry."""
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path)
        binary = tmp_path / "mnemonic-proxy"
        binary.write_text("#!/usr/bin/env python3\n")
        with patch.object(manager, '_get_mnemonic_proxy_binary', return_value=binary), \
             patch.object(mp, '_needs_zstd_shim', return_value=True), \
             patch.object(mp, '_zstd_shim_usable', return_value=True):
            cmd = manager.build_cmd()
        assert cmd[1] == "-c"
        assert "compression_zstd_shim" in cmd[2]
        # arguments start right after the preloader - no duplicated binary path
        assert cmd[3] == "--port"
        # kv_archive stays enabled in the written config
        config = json.loads((tmp_path / "data" / "config.json").read_text())
        assert config["kv_archive"] is True

    def test_build_cmd_plain_without_kv_archive(self, tmp_path):
        import mnemonic_proxy as mp
        manager = self._manager(tmp_path, config_override={"autosave": True})
        binary = tmp_path / "mnemonic-proxy"
        binary.write_text("#!/usr/bin/env python3\n")
        with patch.object(manager, '_get_mnemonic_proxy_binary', return_value=binary), \
             patch.object(mp, '_needs_zstd_shim', return_value=True):
            cmd = manager.build_cmd()
        assert cmd[0] == str(binary)
        assert cmd[1] == "--port"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])