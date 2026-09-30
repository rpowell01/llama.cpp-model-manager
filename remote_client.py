#!/usr/bin/env python3
"""
Remote API client for communicating with llama.cpp server.

This module provides a client for remote model management via REST API.

REMOTE_MODE: This module is used when REMOTE_MODE=true. It provides the
RemoteModelRegistry class for interacting with an external llama.cpp server.
All model lifecycle operations (load, unload, chat) are delegated to the
remote server via REST API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Dict, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("llamaweb")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class RemoteAPIError(Exception):
    """Base exception for remote API errors."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[requests.Response] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ModelNotFoundError(RemoteAPIError):
    """Raised when a model is not found on the remote server."""
    
    def __init__(self, model_name: str, response: Optional[requests.Response] = None):
        super().__init__(f"Model '{model_name}' not found", response=response)
        self.model_name = model_name


# ──────────────────────────────────────────────────────────────────────────────
# Model Data Class
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Model:
    """Represents a model from the remote API."""
    name: str
    repo: str = ""
    filename: str = ""
    model_path: str = ""
    expected_size: int = 0
    port: int = 0
    threads: int = 16
    threads_batch: int = 22
    timeout: int = 3600
    mlock: bool = True
    flash_attn: str = "off"
    no_mmap: bool = True
    parallel: int = 4
    ubatch_size: int = 512
    file_exists: bool = False
    file_size_gb: Optional[float] = None
    # Agent/Tool parameters
    jinja: bool = True
    reasoning_preserve: bool = True
    # CPU-only optimizations
    gpu_layers: int = 0
    cache_type_k: str = "fp16"
    cache_type_v: str = "fp16"
    numa: bool = True
    numa_value: str = "distribute"
    cpu_affinity: str = "auto"
    cpu_strict: bool = False
    batch_size: int = 512
    ctx_size: int = 4096
    # YaRN RoPE scaling
    rope_freq_base: float = 10000.0
    rope_freq_scale: float = 1.0
    rope_scaling_type: str = "none"
    rope_scale: float = 1.0
    yarn_orig_ctx: int = 0
    yarn_ext_factor: float = -1.0
    yarn_attn_factor: float = 1.0
    yarn_beta_slow: float = 1.0
    yarn_beta_fast: float = 32.0
    # Speculative decoding parameters
    spec_type: str = "none"
    spec_draft_model: str = ""
    spec_draft_hf_repo: str = ""
    spec_draft_n_max: int = 3
    spec_draft_n_min: int = 0
    spec_draft_p_split: float = 0.10
    spec_draft_p_min: float = 0.00
    spec_draft_ngl: str = "auto"
    spec_draft_temp: float = 0.0
    spec_draft_top_p: float = 1.0
    spec_draft_min_p: float = 0.0
    spec_draft_typical: float = 1.0
    spec_ngram_size_n: int = 12
    spec_ngram_size_m: int = 48
    spec_ngram_min_hits: int = 1
    # Warmup parameters
    warmup_enabled: bool = False
    warmup_message: str = "Hello"
    warmup_max_tokens: int = 10
    # User ratings and comments
    comment: str = ""
    rating_speed: int = 0  # 1-5 stars
    rating_coding: int = 0  # 1-5 stars
    # Slot KV Cache Persistence
    slot_save_path: str = ""
    cache_reuse: int = 4096

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Model":
        """Create a Model instance from a dictionary."""
        return cls(
            name=data.get("name", ""),
            repo=data.get("repo", ""),
            filename=data.get("filename", ""),
            model_path=data.get("model_path", ""),
            expected_size=data.get("expected_size", 0),
            port=data.get("port", 0),
            threads=data.get("threads", 16),
            threads_batch=data.get("threads_batch", 22),
            timeout=data.get("timeout", 3600),
            mlock=data.get("mlock", True),
            flash_attn=data.get("flash_attn", "off"),
            no_mmap=data.get("no_mmap", True),
            parallel=data.get("parallel", 4),
            ubatch_size=data.get("ubatch_size", 512),
            file_exists=data.get("file_exists", False),
            file_size_gb=data.get("file_size_gb"),
            # Agent/Tool parameters
            jinja=data.get("jinja", True),
            reasoning_preserve=data.get("reasoning_preserve", True),
            # CPU-only optimizations
            gpu_layers=data.get("gpu_layers", 0),
            cache_type_k=data.get("cache_type_k", "fp16"),
            cache_type_v=data.get("cache_type_v", "fp16"),
            numa=data.get("numa", True),
            numa_value=data.get("numa_value", "distribute"),
            cpu_affinity=data.get("cpu_affinity", "auto"),
            cpu_strict=data.get("cpu_strict", False),
            batch_size=data.get("batch_size", 512),
            ctx_size=data.get("ctx_size", 4096),
            # YaRN RoPE scaling
            rope_freq_base=data.get("rope_freq_base", 10000.0),
            rope_freq_scale=data.get("rope_freq_scale", 1.0),
            rope_scaling_type=data.get("rope_scaling_type", "none"),
            rope_scale=data.get("rope_scale", 1.0),
            yarn_orig_ctx=data.get("yarn_orig_ctx", 0),
            yarn_ext_factor=data.get("yarn_ext_factor", -1.0),
            yarn_attn_factor=data.get("yarn_attn_factor", 1.0),
            yarn_beta_slow=data.get("yarn_beta_slow", 1.0),
            yarn_beta_fast=data.get("yarn_beta_fast", 32.0),
            # Speculative decoding
            spec_type=data.get("spec_type", "none"),
            spec_draft_model=data.get("spec_draft_model", ""),
            spec_draft_hf_repo=data.get("spec_draft_hf_repo", ""),
            spec_draft_n_max=data.get("spec_draft_n_max", 3),
            spec_draft_n_min=data.get("spec_draft_n_min", 0),
            spec_draft_p_split=data.get("spec_draft_p_split", 0.10),
            spec_draft_p_min=data.get("spec_draft_p_min", 0.00),
            spec_draft_ngl=data.get("spec_draft_ngl", "auto"),
            spec_draft_temp=data.get("spec_draft_temp", 0.0),
            spec_draft_top_p=data.get("spec_draft_top_p", 1.0),
            spec_draft_min_p=data.get("spec_draft_min_p", 0.0),
            spec_draft_typical=data.get("spec_draft_typical", 1.0),
            spec_ngram_size_n=data.get("spec_ngram_size_n", 12),
            spec_ngram_size_m=data.get("spec_ngram_size_m", 48),
            spec_ngram_min_hits=data.get("spec_ngram_min_hits", 1),
            # Warmup
            warmup_enabled=data.get("warmup_enabled", False),
            warmup_message=data.get("warmup_message", "Hello"),
            warmup_max_tokens=data.get("warmup_max_tokens", 10),
            # User ratings and comments
            comment=data.get("comment", ""),
            rating_speed=data.get("rating_speed", 0),
            rating_coding=data.get("rating_coding", 0),
            # Slot KV Cache Persistence
            slot_save_path=data.get("slot_save_path", ""),
            cache_reuse=data.get("cache_reuse", 4096),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Remote Model Registry
# ──────────────────────────────────────────────────────────────────────────────

class RemoteModelRegistry:
    """
    Client for remote model management via REST API.
    
    Provides methods to list, load, unload models and get server status.
    Uses requests.Session for connection pooling with retry logic.
    
    Supports both custom /api/ endpoints and OpenAI-compatible /v1/ endpoints.
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
    ):
        """
        Initialize the remote client.
        
        Args:
            base_url: Base URL of the remote API server (e.g., "http://localhost:8080")
            timeout: Request timeout in seconds (default: 30)
            max_retries: Maximum number of retry attempts (default: 3)
            backoff_factor: Exponential backoff multiplier (default: 1.0)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        # OpenAI-compatible API path for llama.cpp
        self.api_path = "/v1/"
        
        # Create session with connection pooling and retry adapter
        self._session = requests.Session()
        
        # Configure retry strategy
        # raise_on_status=False: return last response instead of raising on exhaustion
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
    
    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Make an HTTP request with error handling.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (will be appended to base_url)
            **kwargs: Additional arguments passed to requests
            
        Returns:
            Response object
            
        Raises:
            RemoteAPIError: On connection or HTTP errors
        """
        url = urljoin(self.base_url, endpoint)
        kwargs.setdefault("timeout", self.timeout)
        
        try:
            log.debug("Making %s request to %s", method, url)
            response = self._session.request(method, url, **kwargs)
            return response
        except requests.exceptions.Timeout as e:
            raise RemoteAPIError(f"Request timed out after {self.timeout}s") from e
        except requests.exceptions.ConnectionError as e:
            raise RemoteAPIError(f"Failed to connect to {url}") from e
        except requests.exceptions.RequestException as e:
            raise RemoteAPIError(f"Request failed: {e}") from e
    
    def _handle_response(self, response: requests.Response, model_name: str = "unknown") -> Any:
        """
        Process the response and extract data.
        
        Args:
            response: Response object
            model_name: Model name for error messages
            
        Returns:
            Parsed JSON data or None
            
        Raises:
            ModelNotFoundError: When model is not found (404)
            RemoteAPIError: On other HTTP errors
        """
        if response.status_code >= 400:
            if response.status_code == 404:
                raise ModelNotFoundError(model_name)
            raise RemoteAPIError(
                f"HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
                response=response,
            )
        return response.json()
    
    def list_models(self) -> List[Model]:
        """
        List all available models.
        
        Returns:
            List of Model objects
            
        Raises:
            RemoteAPIError: On connection or HTTP errors
        """
        # Use OpenAI-compatible /v1/models endpoint for llama.cpp
        url = self.base_url + self.api_path + "models"
        kwargs = {"timeout": self.timeout}
        try:
            log.debug("Making GET request to %s", url)
            response = self._session.request("GET", url, **kwargs)
            if response.status_code >= 400:
                raise RemoteAPIError(
                    f"HTTP {response.status_code}: {response.text}",
                    status_code=response.status_code,
                    response=response,
                )
            data = response.json()
        except requests.exceptions.Timeout as e:
            raise RemoteAPIError(f"Request timed out after {self.timeout}s") from e
        except requests.exceptions.ConnectionError as e:
            raise RemoteAPIError(f"Failed to connect to {url}") from e
        except requests.exceptions.RequestException as e:
            raise RemoteAPIError(f"Request failed: {e}") from e
        
        if not data:
            return []
        
        # OpenAI format: {"object": "list", "data": [{"id": "...", ...}]}
        models_data = data.get("data", [])
        
        # Convert OpenAI format to Model objects
        result = []
        for m in models_data:
            full_id = m.get("id", "")
            # Extract a cleaner model name from the full path
            # e.g., "C:\Bonsai-demo\models\bonsai2-gguf\27B\Ternary-Bonsai-2-27B-PQ2_0.gguf"
            # -> "Ternary-Bonsai-2-27B-PQ2_0"
            import os
            if full_id:
                # Try to get the filename without extension
                base_name = os.path.basename(full_id)
                name = os.path.splitext(base_name)[0]
            else:
                name = ""
            
            model = Model.from_dict({
                "name": name,
                "repo": "",
                "filename": base_name if full_id else "",
                "model_path": full_id,
                "expected_size": m.get("meta", {}).get("size", 0) or 0,
                "port": 0,
                "threads": 16,
                "threads_batch": 22,
                "timeout": 3600,
                "mlock": True,
                "flash_attn": "off",
                "no_mmap": True,
                "parallel": 4,
                "ubatch_size": 512,
                "file_exists": True,
                "file_size_gb": round(m.get("meta", {}).get("size", 0) / (1024**3), 2) if m.get("meta", {}).get("size") else None,
            })
            result.append(model)
        
        return result
    
    def load_model(self, name: str) -> bool:
        """
        Load a model by name.
        
        Note: llama.cpp OpenAI-compatible server doesn't support dynamic model loading.
        Models are pre-loaded at server startup. This method checks if the model
        exists in the loaded models list.
        
        Args:
            name: Model name to load
            
        Returns:
            True if model is loaded
            
        Raises:
            ModelNotFoundError: If model is not found
            RemoteAPIError: On other connection or HTTP errors
        """
        models = self.list_models()
        for model in models:
            if model.name == name:
                log.info(f"Model '{name}' is already loaded on remote server")
                return True
        raise ModelNotFoundError(name)
    
    def unload_model(self, name: str) -> bool:
        """
        Unload a model by name.
        
        Note: llama.cpp OpenAI-compatible server doesn't support dynamic model unloading.
        This method is a no-op that returns True if the model exists.
        
        Args:
            name: Model name to unload
            
        Returns:
            True if model exists
            
        Raises:
            ModelNotFoundError: If model is not found
            RemoteAPIError: On other connection or HTTP errors
        """
        models = self.list_models()
        for model in models:
            if model.name == name:
                log.info(f"Model '{name}' cannot be unloaded via API (pre-loaded)")
                return True
        raise ModelNotFoundError(name)
    
    def get_model(self, name: str) -> Optional[Model]:
        """
        Get a specific model by name.
        
        Args:
            name: Model name to retrieve
            
        Returns:
            Model object if found, None otherwise
            
        Raises:
            RemoteAPIError: On connection or HTTP errors
        """
        # Check if model is in the list of loaded models
        models = self.list_models()
        for model in models:
            if model.name == name:
                return model
        return None
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get the server status.
        
        Returns:
            Status dictionary from the server
            
        Raises:
            RemoteAPIError: On connection or HTTP errors
        """
        # Call / directly - llama.cpp server returns HTML at root for status
        url = self.base_url + "/"
        kwargs = {"timeout": self.timeout}
        try:
            log.debug("Making GET request to %s", url)
            response = self._session.request("GET", url, **kwargs)
            if response.status_code >= 400:
                raise RemoteAPIError(
                    f"HTTP {response.status_code}: {response.text}",
                    status_code=response.status_code,
                    response=response,
                )
            # llama.cpp returns HTML, not JSON - just return 200 OK as success
            return {"status": "running"}
        except requests.exceptions.Timeout as e:
            raise RemoteAPIError(f"Request timed out after {self.timeout}s") from e
        except requests.exceptions.ConnectionError as e:
            raise RemoteAPIError(f"Failed to connect to {url}") from e
        except requests.exceptions.RequestException as e:
            raise RemoteAPIError(f"Request failed: {e}") from e
    
    def close(self) -> None:
        """Close the session and release resources."""
        self._session.close()
    
    def __enter__(self) -> "RemoteModelRegistry":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
