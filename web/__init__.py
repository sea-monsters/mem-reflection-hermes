"""Web service: FastAPI dashboard endpoints.

This module provides 14 REST endpoints for memory management,
search, graph visualization, and curator status.
"""

from .api import router

__all__ = ["router"]
