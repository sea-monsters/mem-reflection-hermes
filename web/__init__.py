"""Web service: FastAPI dashboard endpoints.

This module provides 14 REST endpoints for memory management,
search, graph visualization, and curator status.
"""

from .api import (
    router,
    get_health,
    get_stats,
    post_search,
    get_graph,
    get_curator,
    get_skills,
    get_profile,
    get_reflect_log,
    get_effectiveness,
    get_zones,
    get_memory,
    delete_memory,
    get_graph_neighbors,
    get_graph_stats,
)

__all__ = [
    "router",
    "get_health",
    "get_stats",
    "post_search",
    "get_graph",
    "get_curator",
    "get_skills",
    "get_profile",
    "get_reflect_log",
    "get_effectiveness",
    "get_zones",
    "get_memory",
    "delete_memory",
    "get_graph_neighbors",
    "get_graph_stats",
]
