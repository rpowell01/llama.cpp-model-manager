#!/usr/bin/env python3
"""
Remote API client for communicating with llama.cpp server.

This module provides a client for remote model management via REST API. 
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
    cache_size: int = 32768
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
            cache_size=data.get("cache_size", 32768),
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
        )


# ──────────────────────────────────────────────────────────────────────────────
# Remote Model Registry
# ──────────────────────────────────────────────────────────────────────────────

class RemoteModelRegistry:
    """
    Client for remote model management via REST API.
    
    Provides methods to list, load, unload models and get server status.
    Uses requests.Session for connection pooling with retry logic.
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
        self.base_url = base_url.rstrip("/") + "/api/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        
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
        response = self._request("GET", "models")
        data = self._handle_response(response)
        
        if not data:
            return []
        
        # Handle both list and dict responses
        if isinstance(data, dict):
            models_data = data.get("models", [])
        else:
            models_data = data
        
        return [Model.from_dict(m) for m in models_data]
    
    def load_model(self, name: str) -> bool:
        """
        Load a model by name.
        
        Args:
            name: Model name to load
            
        Returns:
            True if successful
            
        Raises:
            ModelNotFoundError: If model is not found
            RemoteAPIError: On other connection or HTTP errors
        """
        response = self._request("POST", "models/load", json={"name": name})
        self._handle_response(response, model_name=name)
        return True
    
    def unload_model(self, name: str) -> bool:
        """
        Unload a model by name.
        
        Args:
            name: Model name to unload
            
        Returns:
            True if successful
            
        Raises:
            ModelNotFoundError: If model is not found
            RemoteAPIError: On other connection or HTTP errors
        """
        response = self._request("POST", "models/unload", json={"name": name})
        self._handle_response(response, model_name=name)
        return True
    
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
        try:
            response = self._request("GET", f"models/{name}")
            data = self._handle_response(response, model_name=name)
            return Model.from_dict(data)
        except ModelNotFoundError:
            return None
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get the server status.
        
        Returns:
            Status dictionary from the server
            
        Raises:
            RemoteAPIError: On connection or HTTP errors
        """
        response = self._request("GET", "status")
        return self._handle_response(response)
    
    def close(self) -> None:
        """Close the session and release resources."""
        self._session.close()
    
    def __enter__(self) -> "RemoteModelRegistry":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
