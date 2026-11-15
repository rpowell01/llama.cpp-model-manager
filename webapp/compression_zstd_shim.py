#!/usr/bin/env python3
"""
Shim module to provide `compression.zstd` interface using the `zstandard` package.

This allows mnemonic-proxy's kvarchive module to work on Python < 3.14
by providing the stdlib `compression.zstd` API via the `zstandard` PyPI package.

Usage: Import this module before importing ctxproxy.kvarchive:
    import compression_zstd_shim  # noqa: F401
    from ctxproxy import kvarchive
"""

import sys
import zstandard as zstd

# Create a mock compression module with zstd submodule
class _CompressionZstd:
    """Mimics Python 3.14's compression.zstd module interface."""
    
    @staticmethod
    def compress(data, level=3):
        """Compress data using zstd.
        
        Args:
            data: bytes-like object to compress
            level: compression level (1-22, default 3)
            
        Returns:
            Compressed bytes
        """
        return zstd.compress(data, level)
    
    @staticmethod
    def decompress(data):
        """Decompress zstd-compressed data.
        
        Args:
            data: zstd-compressed bytes
            
        Returns:
            Decompressed bytes
        """
        return zstd.decompress(data)

# Install the shim in sys.modules so `import compression.zstd` works
import types
compression_module = types.ModuleType('compression')
compression_module.zstd = _CompressionZstd()
sys.modules['compression'] = compression_module
sys.modules['compression.zstd'] = _CompressionZstd()

# Also make it available as compression.zstd
compression_module.__all__ = ['zstd']