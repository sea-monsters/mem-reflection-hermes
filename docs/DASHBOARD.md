# Dashboard Memory Manager

The dashboard provides a full-featured UI for managing memories directly:

| Feature | Description |
|---------|-------------|
| **Search** | Real-time filter by content or tags |
| **Zone Filter** | Dropdown to show only memories from a specific zone |
| **Sort** | By rank (default), date, confidence, or zone |
| **Create** | `+ New Memory` button opens edit dialog |
| **Edit** | ✏️ button to modify content, zone, confidence, tags, pinned status |
| **Delete** | 🗑️ button with confirmation dialog |
| **Reorder** | ↑↓ buttons to move memories — persists via explicit `rank` field |

The dashboard communicates with MemoryStore through **atomic store methods** (`update()` and `reorder()`) that handle file I/O, cache invalidation, and index rebuilds in a single operation — preventing the cache inconsistency bugs common in earlier approaches.

## API Endpoints

FastAPI, mounted at `/api/plugins/mem-reflection-hermes/`.
Current surface: 14 routes (v1.0-beta).

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/memories` | List all active memories |
| `POST` | `/memories` | Create new memory (auto-associates in graph) |
| `PUT` | `/memories/{id}` | **Atomic update** (write-then-delete swap, cache + index) |
| `DELETE` | `/memories/{id}` | Delete memory + cleanup graph edges |
| `POST` | `/memories/reorder` | **Atomic reorder** via explicit `rank` assignment |
| `GET` | `/zones` | All zones with counts |
| `GET` | `/graph` | Memory graph (nodes + edges) with real Hebbian edges, SUPERSEDES edges, PageRank scores, SkillStore nodes |
| `GET` | `/graph/neighbors/{id}` | Graph neighbors for a memory with metadata enrichment |
| `GET` | `/graph/zones` | Cross-zone bridge analysis (includes `zone_degree`) |
| `GET` | `/query` | CLUQI cross-layer unified search |
| `GET` | `/skills` | All skills with metadata |
| `GET` | `/reflections` | Recent reflection outcomes (optional `mode` filter) |
| `GET` | `/reflections/audit` | Flattened reflection audit entries (optional `decision` filter) |
| `GET` | `/stats` | Aggregate statistics (memory count, zones, graph stats, cache stats) |

## Reflection Audit Log (v0.9.2-beta2)

The reflection pipeline now writes structured `audit_entries` into each reflect
log record. These entries explain why a candidate was accepted, skipped,
superseded, or rejected.

### Audit Entry Schema

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | string | Unique ID for this candidate |
| `decision` | enum | `accepted` \| `rejected` \| `skipped` \| `superseded` \| `pending` |
| `decision_reason` | string | Human-readable explanation |
| `novelty_score` | float (0-1) | Semantic novelty vs existing memories |
| `conflict_id` | string | Existing memory ID that caused conflict (if any) |
| `supersedes_ids` | string[] | Memory IDs this candidate supersedes |
| `supersedes_reason` | string | Why the supersede was chosen |
| `assigned_zone` | string | Zone assigned to the stored memory |
| `graph_migration` | object | Edge migration metadata (optional) |

### Querying Audit Entries

```bash
# All recent audit entries
GET /reflections/audit?limit=50

# Only accepted decisions
GET /reflections/audit?decision=accepted&limit=20

# Full reflection log with audit trails
GET /reflections?mode=embedding&limit=10
```

Backward compatibility: older log entries without `audit_entries` are returned
with an empty `audit_entries` array.
