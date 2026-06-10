"""Tool schema definitions for mem-reflection-hermes.

All registered Hermes tool schemas live here so the package entrypoint
(__init__.py) stays focused on imports, singleton wiring, and registration.
"""
from __future__ import annotations

_SRH_MEMORY_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string", "description": "Memory content to store"},
        "scope": {"type": "string", "enum": ["user", "project"], "description": "User or project scope"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"], "description": "Confidence level"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags"},
        "pinned": {"type": "boolean", "description": "Pin the memory to the top"},
        "zone": {"type": "string", "description": "Memory zone (general, work, episode, core, or project:<name>)"},
        "supersedes": {"type": "array", "items": {"type": "string"}, "description": "IDs of memories this replaces"},
    },
    "required": ["body"],
}

_SRH_MEMORY_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query text"},
        "k": {"type": "integer", "description": "Maximum results to return (default 5)"},
        "zone": {"type": "string", "description": "Filter to a specific zone"},
        "include_history": {"type": "boolean", "description": "Include superseded memories"},
    },
    "required": ["query"],
}

_SRH_MEMORY_DELETE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Memory ID to delete"},
        "scope": {"type": "string", "enum": ["user", "project"], "description": "User or project scope"},
    },
    "required": ["id"],
}

_SRH_PALACE_NAVIGATE_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "Topic to recall memories for"},
        "limit": {"type": "integer", "description": "Maximum memories to return (default 5)"},
        "zone": {"type": "string", "description": "Specific zone to search, or null for active zone"},
    },
    "required": ["topic"],
}

_SRH_REFLECT_NOW_SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {"type": "array", "description": "Conversation messages to reflect on"},
        "mode": {"type": "string", "enum": ["full", "micro", "embedding"], "description": "Reflection mode"},
    },
    "required": ["messages"],
}

_SRH_SKILL_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Skill search query"},
        "k": {"type": "integer", "description": "Maximum results to return (default 3)"},
    },
    "required": ["query"],
}

_SRH_COMPILE_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["profile", "summary", "stats"], "description": "Compilation mode"},
    },
    "required": ["mode"],
}

_SRH_ASSOCIATE_SCHEMA = {
    "type": "object",
    "properties": {
        "memory_ids": {"type": "array", "items": {"type": "string"}, "description": "Memory IDs to associate (max 20)"},
        "context": {"type": "string", "description": "Optional context string"},
        "relation": {"type": "string", "enum": ["co_occurs", "supersedes", "related"], "description": "Relation type"},
        "seed_ids": {"type": "array", "items": {"type": "string"}, "description": "Seed memory IDs for spreading activation"},
    },
    "required": ["memory_ids"],
}

_SRH_GRAPH_RETRIEVE_SCHEMA = {
    "type": "object",
    "properties": {
        "seed_ids": {"type": "array", "items": {"type": "string"}, "description": "Seed memory IDs to start retrieval from"},
        "max_results": {"type": "integer", "description": "Maximum number of results (default 10)"},
        "tier": {"type": "string", "enum": ["count", "rank", "all"], "description": "Result tier"},
    },
    "required": ["seed_ids"],
}

_SRH_GRAPH_STATS_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": ["adjacency", "nodes", "edges"], "description": "Output format"},
        "depth": {"type": "integer", "description": "Graph traversal depth (default 2)"},
    },
    "required": [],
}

_SRH_GRAPH_VIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": ["adjacency", "nodes", "edges"], "description": "Visualization format"},
        "depth": {"type": "integer", "description": "Graph traversal depth (default 2)"},
    },
    "required": [],
}

_SRH_MEMORY_HEALTH_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}
