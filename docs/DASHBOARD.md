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

FastAPI, mounted at `/api/plugins/mem-reflection-hermes/`:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/memories` | List all active memories |
| `POST` | `/memories` | Create new memory |
| `GET` | `/memories/{id}` | Get single memory |
| `PUT` | `/memories/{id}` | **Atomic update** (write-then-delete swap, cache + index) |
| `DELETE` | `/memories/{id}` | Delete memory |
| `POST` | `/memories/reorder` | **Atomic reorder** via explicit `rank` assignment |
| `GET` | `/zones` | All zones with counts |
| `GET` | `/graph` | Memory graph (nodes + edges) with real Hebbian edges, SUPERSEDES edges, PageRank scores |
| `GET` | `/graph/neighbors/{id}` | Graph neighbors for a memory with metadata enrichment |
| `GET` | `/graph/zones` | Cross-zone bridge analysis |
| `GET` | `/query` | CLUQI cross-layer unified search |
| `GET` | `/skills` | All skills with metadata |
| `GET` | `/reflections` | Recent reflection outcomes |
| `GET` | `/stats` | Aggregate statistics (memory count, zones, graph stats, cache stats) |
