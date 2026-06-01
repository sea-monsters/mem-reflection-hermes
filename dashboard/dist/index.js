(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Tabs, TabsList, TabsTrigger, Input, Textarea, Select, Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, ScrollArea, Separator } = SDK.components;

  // ---------------------------------------------------------------------------
  // Utility hooks
  // ---------------------------------------------------------------------------

  function useMemories() {
    const [memories, setMemories] = React.useState([]);
    const [loading, setLoading] = React.useState(false);

    const fetchMemories = React.useCallback(() => {
      setLoading(true);
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/memories")
        .then(data => setMemories(data.memories || []))
        .catch(console.error)
        .finally(() => setLoading(false));
    }, []);

    React.useEffect(() => { fetchMemories(); }, [fetchMemories]);

    return { memories, loading, refresh: fetchMemories };
  }

  function useZones() {
    const [zones, setZones] = React.useState([]);
    const fetchZones = React.useCallback(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/zones")
        .then(data => setZones(data.zones || []))
        .catch(console.error);
    }, []);
    React.useEffect(() => { fetchZones(); }, [fetchZones]);
    return [zones, fetchZones];
  }

  function useGraph() {
    const [graph, setGraph] = React.useState({ nodes: [], edges: [], stats: {} });
    const [loading, setLoading] = React.useState(false);

    const fetchGraph = React.useCallback((params = {}) => {
      setLoading(true);
      const qs = new URLSearchParams(params).toString();
      SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/graph?${qs}`)
        .then(data => setGraph(data || { nodes: [], edges: [], stats: {} }))
        .catch(console.error)
        .finally(() => setLoading(false));
    }, []);

    return { graph, loading, refresh: fetchGraph };
  }

  function useNeighbors(memId) {
    const [neighbors, setNeighbors] = React.useState([]);
    React.useEffect(() => {
      if (!memId) return;
      SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/graph/neighbors/${memId}`)
        .then(data => setNeighbors(data.neighbors || []))
        .catch(console.error);
    }, [memId]);
    return neighbors;
  }

  // ---------------------------------------------------------------------------
  // Memory Manager Component
  // ---------------------------------------------------------------------------

  function MemoryManager({ memories, zones, onRefresh, onMutate }) {
    const [editing, setEditing] = React.useState(null);
    const [creating, setCreating] = React.useState(false);
    const [searchQuery, setSearchQuery] = React.useState("");
    const [zoneFilter, setZoneFilter] = React.useState("all");
    const [sortBy, setSortBy] = React.useState("rank");

    const filtered = React.useMemo(() => {
      let list = memories;
      if (zoneFilter !== "all") {
        list = list.filter(m => m.zone === zoneFilter);
      }
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        list = list.filter(m => m.body.toLowerCase().includes(q));
      }
      const sortFn = {
        rank: (a, b) => (b.rank || 0) - (a.rank || 0),
        created: (a, b) => new Date(b.created || 0) - new Date(a.created || 0),
        confidence: (a, b) => {
          const map = { high: 3, medium: 2, low: 1 };
          return (map[b.confidence] || 0) - (map[a.confidence] || 0);
        },
        zone: (a, b) => (a.zone || "").localeCompare(b.zone || ""),
      };
      list = [...list].sort(sortFn[sortBy] || sortFn.rank);
      return list;
    }, [memories, zoneFilter, searchQuery, sortBy]);

    const handleDelete = async (id) => {
      if (!confirm("Delete this memory?")) return;
      await SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/memories/${id}`, { method: "DELETE" });
      onMutate();
    };

    const handleReorder = async (id, direction) => {
      const idx = filtered.findIndex(m => m.id === id);
      if (idx < 0) return;
      const swapIdx = direction === "up" ? idx - 1 : idx + 1;
      if (swapIdx < 0 || swapIdx >= filtered.length) return;
      const newOrder = filtered.map(m => m.id);
      [newOrder[idx], newOrder[swapIdx]] = [newOrder[swapIdx], newOrder[idx]];
      await SDK.fetchJSON("/api/plugins/mem-reflection-hermes/memories/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ memory_ids: newOrder }),
      });
      onMutate();
    };

    const handleZoneMove = async (id, newZone) => {
      await SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/memories/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zone: newZone }),
      });
      onMutate();
    };

    return React.createElement("div", { className: "space-y-4" },
      React.createElement("div", { className: "flex gap-2 items-center" },
        React.createElement(Input, {
          placeholder: "Search memories...",
          value: searchQuery,
          onChange: e => setSearchQuery(e.target.value),
          className: "flex-1",
        }),
        React.createElement(Select, {
          value: zoneFilter,
          onValueChange: setZoneFilter,
        }, React.createElement("option", { value: "all" }, "All Zones"),
          ...zones.map(z => React.createElement("option", { key: z.name, value: z.name }, z.name))
        ),
        React.createElement(Select, {
          value: sortBy,
          onValueChange: setSortBy,
        }, React.createElement("option", { value: "rank" }, "Rank"),
          React.createElement("option", { value: "created" }, "Created"),
          React.createElement("option", { value: "confidence" }, "Confidence"),
          React.createElement("option", { value: "zone" }, "Zone")
        ),
        React.createElement(Button, { onClick: () => setCreating(true) }, "+ New")
      ),

      React.createElement(ScrollArea, { className: "h-[500px]" },
        filtered.map(mem => React.createElement(Card, { key: mem.id, className: "mb-2" },
          React.createElement(CardContent, { className: "p-3" },
            React.createElement("div", { className: "flex justify-between items-start" },
              React.createElement("div", { className: "flex-1" },
                React.createElement("div", { className: "text-sm font-medium" }, mem.body.substring(0, 120) + (mem.body.length > 120 ? "..." : "")),
                React.createElement("div", { className: "flex gap-1 mt-1 flex-wrap" },
                  React.createElement(Badge, { variant: "outline" }, mem.zone),
                  React.createElement(Badge, { variant: mem.confidence === "high" ? "default" : "secondary" }, mem.confidence),
                  mem.pinned && React.createElement(Badge, { variant: "default" }, "pinned"),
                  ...(mem.tags || []).map(t => React.createElement(Badge, { key: t, variant: "outline", className: "text-xs" }, t))
                )
              ),
              React.createElement("div", { className: "flex gap-1 ml-2" },
                React.createElement(Button, { size: "sm", variant: "ghost", onClick: () => handleReorder(mem.id, "up") }, "↑"),
                React.createElement(Button, { size: "sm", variant: "ghost", onClick: () => handleReorder(mem.id, "down") }, "↓"),
                React.createElement(Button, { size: "sm", variant: "ghost", onClick: () => setEditing(mem) }, "Edit"),
                React.createElement(Button, { size: "sm", variant: "ghost", onClick: () => handleDelete(mem.id) }, "Del")
              )
            ),
            // Zone move dropdown
            React.createElement("div", { className: "mt-2 flex gap-2 items-center" },
              React.createElement("span", { className: "text-xs text-muted-foreground" }, "Move to:"),
              React.createElement(Select, {
                value: mem.zone,
                onValueChange: (v) => handleZoneMove(mem.id, v),
                className: "w-32 h-7 text-xs",
              }, zones.map(z => React.createElement("option", { key: z.name, value: z.name }, z.name)))
            )
          )
        ))
      ),

      editing && React.createElement(MemoryDialog, {
        memory: editing,
        zones: zones,
        onClose: () => setEditing(null),
        onSave: async (data) => {
          await SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/memories/${editing.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
          setEditing(null);
          onMutate();
        },
      }),

      creating && React.createElement(MemoryDialog, {
        memory: null,
        zones: zones,
        onClose: () => setCreating(false),
        onSave: async (data) => {
          await SDK.fetchJSON("/api/plugins/mem-reflection-hermes/memories", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
          setCreating(false);
          onMutate();
        },
      })
    );
  }

  function MemoryDialog({ memory, zones, onClose, onSave }) {
    const [body, setBody] = React.useState(memory ? memory.body : "");
    const [zone, setZone] = React.useState(memory ? memory.zone : "general");
    const [confidence, setConfidence] = React.useState(memory ? memory.confidence : "medium");
    const [tags, setTags] = React.useState(memory ? (memory.tags || []).join(", ") : "");
    const [pinned, setPinned] = React.useState(memory ? memory.pinned : false);

    return React.createElement(Dialog, { open: true, onOpenChange: onClose },
      React.createElement(DialogContent, null,
        React.createElement(DialogHeader, null,
          React.createElement(DialogTitle, null, memory ? "Edit Memory" : "New Memory")
        ),
        React.createElement("div", { className: "space-y-3" },
          React.createElement(Textarea, {
            placeholder: "Memory content...",
            value: body,
            onChange: e => setBody(e.target.value),
            rows: 4,
          }),
          React.createElement("div", { className: "flex gap-2" },
            React.createElement("div", { className: "flex-1" },
              React.createElement("label", { className: "text-xs" }, "Zone"),
              React.createElement(Select, { value: zone, onValueChange: setZone },
                zones.map(z => React.createElement("option", { key: z.name, value: z.name }, z.name))
              )
            ),
            React.createElement("div", { className: "flex-1" },
              React.createElement("label", { className: "text-xs" }, "Confidence"),
              React.createElement(Select, { value: confidence, onValueChange: setConfidence },
                React.createElement("option", { value: "high" }, "High"),
                React.createElement("option", { value: "medium" }, "Medium"),
                React.createElement("option", { value: "low" }, "Low")
              )
            )
          ),
          React.createElement("div", null,
            React.createElement("label", { className: "text-xs" }, "Tags (comma separated)"),
            React.createElement(Input, { value: tags, onChange: e => setTags(e.target.value) })
          ),
          React.createElement("div", { className: "flex items-center gap-2" },
            React.createElement("input", { type: "checkbox", checked: pinned, onChange: e => setPinned(e.target.checked) }),
            React.createElement("label", { className: "text-sm" }, "Pinned")
          )
        ),
        React.createElement(DialogFooter, null,
          React.createElement(Button, { variant: "outline", onClick: onClose }, "Cancel"),
          React.createElement(Button, { onClick: () => onSave({
            body, zone, confidence,
            tags: tags.split(",").map(t => t.trim()).filter(Boolean),
            pinned,
          })}, "Save")
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Graph Visualization Component (v0.9.2: interactive with node click)
  // ---------------------------------------------------------------------------

  function GraphView({ graph, loading, onNodeClick, selectedNode }) {
    const canvasRef = React.useRef(null);
    const [hoveredNode, setHoveredNode] = React.useState(null);

    React.useEffect(() => {
      if (!canvasRef.current || !graph.nodes.length) return;
      const canvas = canvasRef.current;
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);

      const w = rect.width;
      const h = rect.height;
      const nodeMap = new Map(graph.nodes.map((n, i) => [n.id, { ...n, x: w / 2 + Math.cos(i * 2.4) * Math.min(w, h) * 0.35, y: h / 2 + Math.sin(i * 2.4) * Math.min(w, h) * 0.35 }]));

      // Simple force-directed layout (few iterations)
      for (let iter = 0; iter < 30; iter++) {
        for (const edge of graph.edges) {
          const s = nodeMap.get(edge.source);
          const t = nodeMap.get(edge.target);
          if (!s || !t) continue;
          const dx = t.x - s.x;
          const dy = t.y - s.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = (dist - 80) * 0.01 * (edge.weight || 0.5);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          s.x += fx; s.y += fy;
          t.x -= fx; t.y -= fy;
        }
        for (const n of nodeMap.values()) {
          n.x = Math.max(30, Math.min(w - 30, n.x));
          n.y = Math.max(30, Math.min(h - 30, n.y));
        }
      }

      // Draw edges
      ctx.clearRect(0, 0, w, h);
      for (const edge of graph.edges) {
        const s = nodeMap.get(edge.source);
        const t = nodeMap.get(edge.target);
        if (!s || !t) continue;
        const isHighlighted = selectedNode && (edge.source === selectedNode || edge.target === selectedNode);
        const isNeighbor = selectedNode && (edge.source === selectedNode || edge.target === selectedNode);
        const isDimmed = selectedNode && !isNeighbor;

        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        if (edge.type === "supersedes") {
          ctx.strokeStyle = isHighlighted ? "#ef4444" : (isDimmed ? "rgba(239,68,68,0.1)" : "rgba(239,68,68,0.5)");
          ctx.lineWidth = isHighlighted ? 3 : 1.5;
        } else if (edge.type === "skill") {
          ctx.strokeStyle = isHighlighted ? "#8b5cf6" : (isDimmed ? "rgba(139,92,246,0.1)" : "rgba(139,92,246,0.4)");
          ctx.lineWidth = isHighlighted ? 2.5 : 1;
        } else {
          ctx.strokeStyle = isHighlighted ? "#3b82f6" : (isDimmed ? "rgba(59,130,246,0.05)" : "rgba(59,130,246,0.2)");
          ctx.lineWidth = isHighlighted ? 2.5 : 1;
        }
        ctx.stroke();
      }

      // Draw nodes
      for (const n of nodeMap.values()) {
        const isSelected = selectedNode === n.id;
        const isNeighbor = selectedNode && graph.edges.some(e =>
          (e.source === selectedNode && e.target === n.id) ||
          (e.target === selectedNode && e.source === n.id)
        );
        const isDimmed = selectedNode && !isSelected && !isNeighbor;

        const radius = n.type === "skill" ? 8 : (6 + (n.pagerank || 0) * 10);
        ctx.beginPath();
        ctx.arc(n.x, n.y, radius, 0, Math.PI * 2);
        if (n.type === "skill") {
          ctx.fillStyle = isDimmed ? "rgba(139,92,246,0.2)" : (isSelected ? "#7c3aed" : "#8b5cf6");
        } else if (n.zone === "core") {
          ctx.fillStyle = isDimmed ? "rgba(239,68,68,0.2)" : (isSelected ? "#dc2626" : "#ef4444");
        } else if (n.zone === "work") {
          ctx.fillStyle = isDimmed ? "rgba(59,130,246,0.2)" : (isSelected ? "#2563eb" : "#3b82f6");
        } else {
          ctx.fillStyle = isDimmed ? "rgba(107,114,128,0.2)" : (isSelected ? "#4b5563" : "#6b7280");
        }
        ctx.fill();
        if (isSelected) {
          ctx.strokeStyle = "#fbbf24";
          ctx.lineWidth = 3;
          ctx.stroke();
        }

        // Label
        if (!isDimmed || isSelected) {
          ctx.fillStyle = isDimmed ? "rgba(0,0,0,0.3)" : "#1f2937";
          ctx.font = isSelected ? "bold 11px sans-serif" : "10px sans-serif";
          ctx.textAlign = "center";
          ctx.fillText(n.label.substring(0, 20), n.x, n.y + radius + 12);
        }
      }

      // Store node positions for click detection
      canvas._nodePositions = Array.from(nodeMap.values());
    }, [graph, selectedNode]);

    const handleCanvasClick = (e) => {
      if (!canvasRef.current || !canvasRef.current._nodePositions) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      for (const n of canvasRef.current._nodePositions) {
        const dx = x - n.x;
        const dy = y - n.y;
        if (dx * dx + dy * dy < 225) { // 15px radius
          onNodeClick(n.id);
          return;
        }
      }
      onNodeClick(null);
    };

    if (loading) return React.createElement("div", { className: "p-4 text-center" }, "Loading graph...");
    if (!graph.nodes.length) return React.createElement("div", { className: "p-4 text-center text-muted-foreground" }, "No graph data available.");

    return React.createElement("div", { className: "space-y-3" },
      React.createElement("div", { className: "flex gap-2 text-xs text-muted-foreground" },
        React.createElement("span", null, `Nodes: ${graph.stats.node_count || graph.nodes.length}`),
        React.createElement("span", null, `Edges: ${graph.stats.edge_count || graph.edges.length}`),
        graph.stats.hebbian_edges !== undefined && React.createElement("span", null, `Hebbian: ${graph.stats.hebbian_edges}`),
        graph.stats.supersedes_edges !== undefined && React.createElement("span", null, `Supersedes: ${graph.stats.supersedes_edges}`),
        graph.stats.pagerank_computed && React.createElement("span", { className: "text-green-600" }, "PageRank ✓")
      ),
      React.createElement("canvas", {
        ref: canvasRef,
        className: "w-full h-[400px] border rounded cursor-pointer",
        onClick: handleCanvasClick,
      }),
      selectedNode && React.createElement(NodeDetailPanel, { nodeId: selectedNode, graph, onClose: () => onNodeClick(null) })
    );
  }

  function NodeDetailPanel({ nodeId, graph, onClose }) {
    const node = graph.nodes.find(n => n.id === nodeId);
    const neighbors = graph.edges.filter(e => e.source === nodeId || e.target === nodeId);
    const neighborNodes = neighbors.map(e => {
      const nid = e.source === nodeId ? e.target : e.source;
      return graph.nodes.find(n => n.id === nid);
    }).filter(Boolean);

    return React.createElement(Card, { className: "mt-2" },
      React.createElement(CardHeader, { className: "pb-2" },
        React.createElement("div", { className: "flex justify-between items-center" },
          React.createElement(CardTitle, { className: "text-sm" }, "Node Details"),
          React.createElement(Button, { size: "sm", variant: "ghost", onClick: onClose }, "×")
        )
      ),
      React.createElement(CardContent, { className: "text-sm space-y-2" },
        node && React.createElement("div", null,
          React.createElement("div", { className: "font-medium" }, node.label),
          React.createElement("div", { className: "flex gap-2 mt-1" },
            React.createElement(Badge, { variant: "outline" }, node.zone || "general"),
            node.pagerank !== undefined && React.createElement(Badge, { variant: "secondary" }, `PR: ${node.pagerank}`),
            React.createElement(Badge, { variant: "outline" }, `${neighbors.length} edges`)
          )
        ),
        React.createElement("div", { className: "text-xs text-muted-foreground" }, "Neighbors:"),
        React.createElement("div", { className: "space-y-1 max-h-[150px] overflow-y-auto" },
          neighbors.map((e, i) => {
            const nid = e.source === nodeId ? e.target : e.source;
            const n = neighborNodes.find(n => n.id === nid);
            return React.createElement("div", { key: i, className: "flex justify-between text-xs p-1 bg-muted rounded" },
              React.createElement("span", null, n ? n.label.substring(0, 40) : nid),
              React.createElement("span", { className: "text-muted-foreground" }, `${e.relation} (${e.weight})`)
            );
          })
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Skills Component
  // ---------------------------------------------------------------------------

  function SkillsView() {
    const [skills, setSkills] = React.useState([]);
    React.useEffect(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/skills")
        .then(data => setSkills(data.skills || []))
        .catch(console.error);
    }, []);

    return React.createElement("div", { className: "space-y-3" },
      skills.map(sk => React.createElement(Card, { key: sk.name },
        React.createElement(CardContent, { className: "p-3" },
          React.createElement("div", { className: "font-medium" }, sk.name),
          React.createElement("div", { className: "text-sm text-muted-foreground" }, sk.description || "No description"),
          React.createElement("div", { className: "flex gap-1 mt-1 flex-wrap" },
            sk.always_active && React.createElement(Badge, { variant: "default" }, "always-active"),
            ...(sk.triggers || []).map(t => React.createElement(Badge, { key: t, variant: "outline", className: "text-xs" }, t))
          )
        )
      ))
    );
  }

  // ---------------------------------------------------------------------------
  // Reflections Component
  // ---------------------------------------------------------------------------

  function ReflectionsView() {
    const [reflections, setReflections] = React.useState([]);
    React.useEffect(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/reflections")
        .then(data => setReflections(data.reflections || []))
        .catch(console.error);
    }, []);

    return React.createElement("div", { className: "space-y-3" },
      reflections.map((r, i) => React.createElement(Card, { key: i },
        React.createElement(CardContent, { className: "p-3" },
          React.createElement("div", { className: "text-xs text-muted-foreground" },
            new Date(r.timestamp).toLocaleString(), " · ", r.outcome || "unknown"
          ),
          React.createElement("div", { className: "text-sm mt-1" }, r.summary || JSON.stringify(r).substring(0, 200))
        )
      ))
    );
  }

  // ---------------------------------------------------------------------------
  // Stats Component
  // ---------------------------------------------------------------------------

  function StatsView() {
    const [stats, setStats] = React.useState(null);
    React.useEffect(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/stats")
        .then(data => setStats(data))
        .catch(console.error);
    }, []);

    if (!stats) return React.createElement("div", { className: "p-4" }, "Loading stats...");

    return React.createElement("div", { className: "space-y-4" },
      React.createElement(Card, null,
        React.createElement(CardHeader, null, React.createElement(CardTitle, { className: "text-sm" }, "Memory Overview")),
        React.createElement(CardContent, null,
          React.createElement("div", { className: "text-2xl font-bold" }, stats.memory_count),
          React.createElement("div", { className: "text-sm text-muted-foreground" }, "Total active memories")
        )
      ),
      React.createElement(Card, null,
        React.createElement(CardHeader, null, React.createElement(CardTitle, { className: "text-sm" }, "Zones")),
        React.createElement(CardContent, null,
          Object.entries(stats.zones || {}).map(([z, c]) =>
            React.createElement("div", { key: z, className: "flex justify-between text-sm" },
              React.createElement("span", null, z),
              React.createElement("span", { className: "font-medium" }, c)
            )
          )
        )
      ),
      stats.graph && stats.graph.available && React.createElement(Card, null,
        React.createElement(CardHeader, null, React.createElement(CardTitle, { className: "text-sm" }, "Graph Stats")),
        React.createElement(CardContent, null,
          React.createElement("div", { className: "grid grid-cols-2 gap-2 text-sm" },
            React.createElement("div", null, React.createElement("span", { className: "text-muted-foreground" }, "Nodes: "), stats.graph.node_count),
            React.createElement("div", null, React.createElement("span", { className: "text-muted-foreground" }, "Edges: "), stats.graph.edge_count),
            React.createElement("div", null, React.createElement("span", { className: "text-muted-foreground" }, "Avg Weight: "), stats.graph.avg_weight)
          )
        )
      ),
      stats.cache && stats.cache.available && React.createElement(Card, null,
        React.createElement(CardHeader, null, React.createElement(CardTitle, { className: "text-sm" }, "Query Cache")),
        React.createElement(CardContent, null,
          React.createElement("div", { className: "grid grid-cols-2 gap-2 text-sm" },
            React.createElement("div", null, React.createElement("span", { className: "text-muted-foreground" }, "Size: "), stats.cache.size),
            React.createElement("div", null, React.createElement("span", { className: "text-muted-foreground" }, "Hit Rate: "), `${(stats.cache.hit_rate * 100).toFixed(1)}%`)
          )
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Zone Analysis Component (v0.9.2)
  // ---------------------------------------------------------------------------

  function ZoneAnalysisView() {
    const [data, setData] = React.useState(null);
    React.useEffect(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/graph/zones")
        .then(d => setData(d))
        .catch(console.error);
    }, []);

    if (!data) return React.createElement("div", { className: "p-4" }, "Loading zone analysis...");

    return React.createElement("div", { className: "space-y-4" },
      React.createElement(Card, null,
        React.createElement(CardHeader, null, React.createElement(CardTitle, { className: "text-sm" }, "Zone Centrality")),
        React.createElement(CardContent, null,
          Object.entries(data.zone_centrality || {}).sort((a, b) => b[1] - a[1]).map(([z, s]) =>
            React.createElement("div", { key: z, className: "flex justify-between text-sm mb-1" },
              React.createElement("span", null, z),
              React.createElement("div", { className: "w-32 bg-muted rounded h-4 overflow-hidden" },
                React.createElement("div", { className: "bg-blue-500 h-full", style: { width: `${s * 100}%` } })
              )
            )
          )
        )
      ),
      (data.bridge_memories || []).length > 0 && React.createElement(Card, null,
        React.createElement(CardHeader, null, React.createElement(CardTitle, { className: "text-sm" }, `Bridge Memories (${data.total_bridge_memories || 0})`)),
        React.createElement(CardContent, null,
          React.createElement(ScrollArea, { className: "h-[300px]" },
            (data.bridge_memories || []).slice(0, 20).map((b, i) =>
              React.createElement("div", { key: i, className: "text-sm p-2 border-b last:border-0" },
                React.createElement("div", { className: "font-medium" }, b.source_body || b.memory_id),
                React.createElement("div", { className: "text-xs text-muted-foreground" },
                  `Zone: ${b.zone} · Bridge strength: ${(b.bridge_strength || 0).toFixed(2)}`
                ),
                React.createElement("div", { className: "text-xs mt-1" },
                  (b.cross_zone_edges || []).slice(0, 3).map((e, j) =>
                    React.createElement("span", { key: j, className: "mr-2 inline-block bg-muted px-1 rounded" },
                      `→ ${e.target_zone} (${e.weight.toFixed(2)})`
                    )
                  )
                )
              )
            )
          )
        )
      ),
      (data.isolated_zones || []).length > 0 && React.createElement("div", { className: "text-sm text-amber-600" },
        `Isolated zones: ${(data.isolated_zones || []).join(", ")}`
      )
    );
  }

  // ---------------------------------------------------------------------------
  // CLUQI Query Component (v0.9.2)
  // ---------------------------------------------------------------------------

  function CLUQIQueryView() {
    const [query, setQuery] = React.useState("");
    const [results, setResults] = React.useState([]);
    const [loading, setLoading] = React.useState(false);

    const handleSearch = async () => {
      if (!query.trim()) return;
      setLoading(true);
      try {
        const data = await SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/query?q=${encodeURIComponent(query)}&k=10`);
        setResults(data.results || []);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };

    return React.createElement("div", { className: "space-y-4" },
      React.createElement("div", { className: "flex gap-2" },
        React.createElement(Input, {
          placeholder: "Cross-layer unified query...",
          value: query,
          onChange: e => setQuery(e.target.value),
          onKeyDown: e => e.key === "Enter" && handleSearch(),
          className: "flex-1",
        }),
        React.createElement(Button, { onClick: handleSearch, disabled: loading }, loading ? "..." : "Search")
      ),
      results.length > 0 && React.createElement("div", { className: "text-xs text-muted-foreground" },
        `${results.length} results from layers: ${[...new Set(results.flatMap(r => r.sources))].join(", ")}`
      ),
      React.createElement(ScrollArea, { className: "h-[400px]" },
        results.map((r, i) => React.createElement(Card, { key: i, className: "mb-2" },
          React.createElement(CardContent, { className: "p-3" },
            React.createElement("div", { className: "flex justify-between items-start" },
              React.createElement("div", { className: "text-sm font-medium" }, r.metadata?.body || r.memory_id),
              React.createElement(Badge, { variant: "secondary" }, `Score: ${r.score}`)
            ),
            React.createElement("div", { className: "flex gap-1 mt-1 flex-wrap" },
              React.createElement(Badge, { variant: "outline", className: "text-xs" }, r.metadata?.zone || "general"),
              ...(r.metadata?.tags || []).map(t => React.createElement(Badge, { key: t, variant: "outline", className: "text-xs" }, t)),
              ...r.sources.map(s => React.createElement(Badge, { key: s, variant: s === "memory" ? "default" : (s === "graph" ? "secondary" : "outline"), className: "text-xs" }, s))
            ),
            React.createElement("div", { className: "text-xs text-muted-foreground mt-1" },
              `Memory: ${(r.layer_scores?.memory || 0).toFixed(2)} · Graph: ${(r.layer_scores?.graph || 0).toFixed(2)} · Supersedes: ${(r.layer_scores?.supersedes || 0).toFixed(2)}`
            )
          )
        ))
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Main Page
  // ---------------------------------------------------------------------------

  function MemoryGraphPage() {
    const { memories, loading: memLoading, refresh: refreshMemories } = useMemories();
    const [zones] = useZones();
    const { graph, loading: graphLoading, refresh: refreshGraph } = useGraph();
    const [selectedNode, setSelectedNode] = React.useState(null);
    const [activeTab, setActiveTab] = React.useState("memories");

    const handleMutate = () => {
      refreshMemories();
      refreshGraph();
      setSelectedNode(null);
    };

    React.useEffect(() => {
      refreshGraph();
    }, []);

    return React.createElement("div", { className: "space-y-4 p-4" },
      React.createElement("div", { className: "flex justify-between items-center" },
        React.createElement("h2", { className: "text-lg font-bold" }, "Memory Palace Dashboard"),
        React.createElement("div", { className: "text-xs text-muted-foreground" }, "v0.9.2-beta")
      ),

      React.createElement(Tabs, { value: activeTab, onValueChange: setActiveTab },
        React.createElement(TabsList, null,
          React.createElement(TabsTrigger, { value: "memories" }, "Memories"),
          React.createElement(TabsTrigger, { value: "graph" }, "Graph"),
          React.createElement(TabsTrigger, { value: "zones" }, "Zones"),
          React.createElement(TabsTrigger, { value: "query" }, "CLUQI"),
          React.createElement(TabsTrigger, { value: "skills" }, "Skills"),
          React.createElement(TabsTrigger, { value: "reflections" }, "Reflections"),
          React.createElement(TabsTrigger, { value: "stats" }, "Stats")
        ),

        React.createElement("div", { className: "mt-4" },
          activeTab === "memories" && React.createElement(MemoryManager, {
            memories, zones, onRefresh: refreshMemories, onMutate: handleMutate,
          }),
          activeTab === "graph" && React.createElement(GraphView, {
            graph, loading: graphLoading,
            onNodeClick: setSelectedNode, selectedNode,
          }),
          activeTab === "zones" && React.createElement(ZoneAnalysisView),
          activeTab === "query" && React.createElement(CLUQIQueryView),
          activeTab === "skills" && React.createElement(SkillsView),
          activeTab === "reflections" && React.createElement(ReflectionsView),
          activeTab === "stats" && React.createElement(StatsView),
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Register plugin
  // ---------------------------------------------------------------------------

  window.__HERMES_PLUGINS__.register("mem-reflection-hermes", MemoryGraphPage);

})();
