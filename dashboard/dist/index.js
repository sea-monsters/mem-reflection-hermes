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
    React.useEffect(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/zones")
        .then(data => setZones(data.zones || []))
        .catch(console.error);
    }, []);
    return zones;
  }

  // ---------------------------------------------------------------------------
  // Memory Manager Component (NEW)
  // ---------------------------------------------------------------------------

  function MemoryManager({ memories, zones, onRefresh, onMutate }) {
    const [editing, setEditing] = React.useState(null);
    const [creating, setCreating] = React.useState(false);
    const [searchQuery, setSearchQuery] = React.useState("");
    const [zoneFilter, setZoneFilter] = React.useState("all");
    const [sortBy, setSortBy] = React.useState("created"); // created | confidence | zone

    const filtered = React.useMemo(() => {
      let list = [...memories];
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        list = list.filter(m => m.body.toLowerCase().includes(q) || m.tags.some(t => t.toLowerCase().includes(q)));
      }
      if (zoneFilter !== "all") {
        list = list.filter(m => m.zone === zoneFilter);
      }
      if (sortBy === "created") {
        list.sort((a, b) => new Date(b.created) - new Date(a.created));
      } else if (sortBy === "confidence") {
        const order = { high: 0, medium: 1, low: 2 };
        list.sort((a, b) => (order[a.confidence] || 99) - (order[b.confidence] || 99));
      } else if (sortBy === "zone") {
        list.sort((a, b) => a.zone.localeCompare(b.zone));
      }
      return list;
    }, [memories, searchQuery, zoneFilter, sortBy]);

    const handleDelete = async (id) => {
      if (!confirm("Delete this memory? This cannot be undone.")) return;
      try {
        await SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/memories/${id}`, { method: "DELETE" });
        onRefresh();
        if (onMutate) onMutate();
      } catch (e) {
        alert("Failed to delete: " + e.message);
      }
    };

    const handleSave = async (memory) => {
      try {
        if (memory._isNew) {
          await SDK.fetchJSON("/api/plugins/mem-reflection-hermes/memories", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(memory),
          });
        } else {
          await SDK.fetchJSON(`/api/plugins/mem-reflection-hermes/memories/${memory.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              body: memory.body,
              zone: memory.zone,
              confidence: memory.confidence,
              tags: memory.tags,
              pinned: memory.pinned,
            }),
          });
        }
        setEditing(null);
        setCreating(false);
        onRefresh();
        if (onMutate) onMutate();
      } catch (e) {
        alert("Failed to save: " + e.message);
      }
    };

    const handleReorder = async (newOrder) => {
      try {
        await SDK.fetchJSON("/api/plugins/mem-reflection-hermes/memories/reorder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ memory_ids: newOrder.map(m => m.id) }),
        });
        onRefresh();
        if (onMutate) onMutate();
      } catch (e) {
        alert("Failed to reorder: " + e.message);
      }
    };

    const moveItem = (index, direction) => {
      // Reorder must operate on the full canonical list, not filtered subset
      if (searchQuery || zoneFilter !== "all") {
        alert("Please clear search and zone filters before reordering.");
        return;
      }
      const newList = [...filtered];
      const targetIndex = index + direction;
      if (targetIndex < 0 || targetIndex >= newList.length) return;
      [newList[index], newList[targetIndex]] = [newList[targetIndex], newList[index]];
      handleReorder(newList);
    };

    return React.createElement("div", { className: "space-y-4" },
      // Toolbar
      React.createElement("div", { className: "flex flex-wrap gap-3 items-center" },
        React.createElement(Input, {
          placeholder: "Search memories...",
          value: searchQuery,
          onChange: e => setSearchQuery(e.target.value),
          className: "w-64",
        }),
        React.createElement(Select, {
          value: zoneFilter,
          onValueChange: setZoneFilter,
        },
          React.createElement("option", { value: "all" }, "All Zones"),
          zones.map(z => React.createElement("option", { key: z.zone, value: z.zone }, `${z.zone} (${z.count})`))
        ),
        React.createElement(Select, {
          value: sortBy,
          onValueChange: setSortBy,
        },
          React.createElement("option", { value: "created" }, "Sort by Date"),
          React.createElement("option", { value: "confidence" }, "Sort by Confidence"),
          React.createElement("option", { value: "zone" }, "Sort by Zone")
        ),
        React.createElement(Button, { onClick: () => setCreating(true), variant: "default" }, "+ New Memory")
      ),

      // Memory count
      React.createElement("div", { className: "text-sm text-muted-foreground" },
        `Showing ${filtered.length} of ${memories.length} memories`
      ),

      // Memory list
      React.createElement(ScrollArea, { className: "h-[600px]" },
        React.createElement("div", { className: "space-y-2 pr-4" },
          filtered.map((m, idx) =>
            React.createElement(Card, { key: m.id, className: "group" },
              React.createElement(CardContent, { className: "py-3" },
                React.createElement("div", { className: "flex items-start justify-between gap-3" },
                  React.createElement("div", { className: "flex-1 min-w-0" },
                    React.createElement("div", { className: "flex items-center gap-2 mb-2 flex-wrap" },
                      m.pinned && React.createElement(Badge, { variant: "default" }, "📌 Pinned"),
                      React.createElement(Badge, { variant: "outline" }, m.confidence),
                      React.createElement(Badge, { variant: "secondary" }, m.zone),
                      React.createElement("span", { className: "text-xs text-muted-foreground" }, m.scope),
                      m.tags.map(t => React.createElement(Badge, { key: t, variant: "outline", className: "text-xs" }, t))
                    ),
                    React.createElement("pre", { className: "text-sm whitespace-pre-wrap font-sans" }, m.body),
                    React.createElement("div", { className: "text-xs text-muted-foreground mt-2" },
                      `ID: ${m.id} • Created: ${new Date(m.created).toLocaleString()}`
                    )
                  ),
                  React.createElement("div", { className: "flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity" },
                    React.createElement(Button, { size: "sm", variant: "ghost", onClick: () => moveItem(idx, -1), disabled: idx === 0 }, "↑"),
                    React.createElement(Button, { size: "sm", variant: "ghost", onClick: () => moveItem(idx, 1), disabled: idx === filtered.length - 1 }, "↓"),
                    React.createElement(Button, { size: "sm", variant: "ghost", onClick: () => setEditing(m) }, "✏️"),
                    React.createElement(Button, { size: "sm", variant: "ghost", className: "text-destructive", onClick: () => handleDelete(m.id) }, "🗑️")
                  )
                )
              )
            )
          )
        )
      ),

      // Edit Dialog
      (editing || creating) && React.createElement(MemoryEditDialog, {
        memory: editing || { body: "", zone: "general", confidence: "medium", tags: [], pinned: false, _isNew: true },
        zones: zones.map(z => z.zone),
        onSave: handleSave,
        onClose: () => { setEditing(null); setCreating(false); },
        isNew: creating,
      })
    );
  }

  function MemoryEditDialog({ memory, zones, onSave, onClose, isNew }) {
    const [form, setForm] = React.useState({ ...memory });

    const update = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

    return React.createElement(Dialog, { open: true, onOpenChange: onClose },
      React.createElement(DialogContent, { className: "max-w-2xl" },
        React.createElement(DialogHeader, null,
          React.createElement(DialogTitle, null, isNew ? "Create New Memory" : "Edit Memory")
        ),
        React.createElement("div", { className: "space-y-4 py-4" },
          React.createElement("div", null,
            React.createElement("label", { className: "text-sm font-medium" }, "Content"),
            React.createElement(Textarea, {
              value: form.body,
              onChange: e => update("body", e.target.value),
              rows: 8,
              placeholder: "Memory content...",
            })
          ),
          React.createElement("div", { className: "grid grid-cols-2 gap-4" },
            React.createElement("div", null,
              React.createElement("label", { className: "text-sm font-medium" }, "Zone"),
              React.createElement(Select, {
                value: form.zone,
                onValueChange: v => update("zone", v),
              },
                ["core", "work", "episode", "general"].map(z =>
                  React.createElement("option", { key: z, value: z }, z)
                ),
                zones.filter(z => !["core", "work", "episode", "general"].includes(z)).map(z =>
                  React.createElement("option", { key: z, value: z }, z)
                )
              )
            ),
            React.createElement("div", null,
              React.createElement("label", { className: "text-sm font-medium" }, "Confidence"),
              React.createElement(Select, {
                value: form.confidence,
                onValueChange: v => update("confidence", v),
              },
                React.createElement("option", { value: "high" }, "high"),
                React.createElement("option", { value: "medium" }, "medium"),
                React.createElement("option", { value: "low" }, "low")
              )
            )
          ),
          React.createElement("div", null,
            React.createElement("label", { className: "text-sm font-medium" }, "Tags (comma-separated)"),
            React.createElement(Input, {
              value: (form.tags || []).join(", "),
              onChange: e => update("tags", e.target.value.split(",").map(t => t.trim()).filter(Boolean)),
              placeholder: "tag1, tag2, tag3",
            })
          ),
          React.createElement("div", { className: "flex items-center gap-2" },
            React.createElement("input", {
              type: "checkbox",
              id: "pinned",
              checked: form.pinned,
              onChange: e => update("pinned", e.target.checked),
            }),
            React.createElement("label", { htmlFor: "pinned", className: "text-sm" }, "Pin this memory (always included in context)")
          )
        ),
        React.createElement(DialogFooter, null,
          React.createElement(Button, { variant: "outline", onClick: onClose }, "Cancel"),
          React.createElement(Button, { onClick: () => onSave(form) }, isNew ? "Create" : "Save")
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Existing Graph Component
  // ---------------------------------------------------------------------------

  function MemoryGraph({ nodes, edges }) {
    const svgRef = React.useRef(null);
    const [selected, setSelected] = React.useState(null);
    const width = 800;
    const height = 500;

    const nodePositions = React.useMemo(() => {
      const pos = {};
      const memNodes = nodes.filter(n => n.type === "memory");
      const skillNodes = nodes.filter(n => n.type === "skill");

      memNodes.forEach((n, i) => {
        const y = height * 0.1 + (height * 0.8 / Math.max(memNodes.length, 1)) * i;
        pos[n.id] = { x: width * 0.25, y, ...n };
      });
      skillNodes.forEach((n, i) => {
        const y = height * 0.1 + (height * 0.8 / Math.max(skillNodes.length, 1)) * i;
        pos[n.id] = { x: width * 0.75, y, ...n };
      });
      return pos;
    }, [nodes]);

    return React.createElement("div", { className: "relative" },
      React.createElement("svg", {
        ref: svgRef,
        viewBox: `0 0 ${width} ${height}`,
        className: "w-full border rounded-lg",
        style: { background: "var(--color-card)", borderColor: "var(--color-border)" }
      },
        edges.map((e, i) => {
          const s = nodePositions[e.source];
          const t = nodePositions[e.target];
          if (!s || !t) return null;
          return React.createElement("line", {
            key: i,
            x1: s.x, y1: s.y,
            x2: t.x, y2: t.y,
            stroke: e.type === "supersedes" ? "var(--color-destructive)" : "var(--color-muted-foreground)",
            strokeWidth: e.type === "supersedes" ? 2 : 1,
            strokeDasharray: e.type === "supersedes" ? "5,5" : "none",
            opacity: 0.6,
          });
        }),
        Object.values(nodePositions).map((n) =>
          React.createElement("g", {
            key: n.id,
            transform: `translate(${n.x}, ${n.y})`,
            onClick: () => setSelected(n),
            style: { cursor: "pointer" }
          },
            React.createElement("circle", {
              r: n.type === "memory" ? (n.pinned ? 14 : 10) : 12,
              fill: n.type === "memory"
                ? (n.pinned ? "var(--color-primary)" : "var(--color-accent)")
                : "var(--color-secondary)",
              stroke: selected && selected.id === n.id ? "var(--color-ring)" : "var(--color-border)",
              strokeWidth: selected && selected.id === n.id ? 3 : 1,
            }),
            React.createElement("text", {
              x: 0, y: n.type === "memory" ? (n.pinned ? 18 : 14) : 16,
              textAnchor: "middle",
              fill: "var(--color-card-foreground)",
              fontSize: "10px",
              fontFamily: "var(--font-mono)",
            }, n.label.substring(0, 20))
          )
        )
      ),
      selected && React.createElement(Card, { className: "mt-4" },
        React.createElement(CardHeader, null,
          React.createElement(CardTitle, null,
            selected.type === "memory" ? "🧠 Memory" : "🔧 Skill",
            " ",
            React.createElement(Badge, { variant: "outline" }, selected.scope)
          )
        ),
        React.createElement(CardContent, null,
          React.createElement("pre", { className: "text-xs overflow-auto" },
            JSON.stringify(selected, null, 2)
          )
        )
      )
    );
  }

  function StatsPanel({ stats }) {
    if (!stats) return null;
    return React.createElement("div", { className: "grid grid-cols-4 gap-4 mb-6" },
      React.createElement(Card, null,
        React.createElement(CardHeader, null,
          React.createElement(CardTitle, { className: "text-sm" }, "Memories")
        ),
        React.createElement(CardContent, null,
          React.createElement("div", { className: "text-2xl font-bold" }, stats.memory_count)
        )
      ),
      React.createElement(Card, null,
        React.createElement(CardHeader, null,
          React.createElement(CardTitle, { className: "text-sm" }, "Skills")
        ),
        React.createElement(CardContent, null,
          React.createElement("div", { className: "text-2xl font-bold" }, stats.skill_count)
        )
      ),
      React.createElement(Card, null,
        React.createElement(CardHeader, null,
          React.createElement(CardTitle, { className: "text-sm" }, "Zones")
        ),
        React.createElement(CardContent, null,
          React.createElement("div", { className: "text-2xl font-bold" },
            stats.memories_by_zone ? Object.keys(stats.memories_by_zone).length : 0
          )
        )
      ),
      React.createElement(Card, null,
        React.createElement(CardHeader, null,
          React.createElement(CardTitle, { className: "text-sm" }, "Top Tag")
        ),
        React.createElement(CardContent, null,
          React.createElement("div", { className: "text-lg font-bold" },
            stats.top_tags && stats.top_tags[0] ? stats.top_tags[0][0] : "—"
          )
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Main Page Component
  // ---------------------------------------------------------------------------

  function MemoryGraphPage() {
    const [graph, setGraph] = React.useState({ nodes: [], edges: [] });
    const [stats, setStats] = React.useState(null);
    const [reflections, setReflections] = React.useState([]);
    const [activeTab, setActiveTab] = React.useState("manager");
    const { memories, refresh } = useMemories();
    const zones = useZones();

    // Global refresh: re-fetches graph, stats, reflections, and memories
    const refreshAll = React.useCallback(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/graph")
        .then(setGraph)
        .catch(console.error);
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/stats")
        .then(setStats)
        .catch(console.error);
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/reflections")
        .then(r => setReflections(r.reflections || []))
        .catch(console.error);
      refresh();
    }, [refresh]);

    React.useEffect(() => {
      refreshAll();
    }, [refreshAll]);

    return React.createElement("div", { className: "p-6 space-y-6" },
      React.createElement("h1", { className: "text-2xl font-bold" }, "Memory & Reflection"),
      React.createElement(StatsPanel, { stats }),
      React.createElement(Tabs, { value: activeTab, onValueChange: setActiveTab },
        React.createElement(TabsList, null,
          React.createElement(TabsTrigger, { value: "manager" }, "📝 Memory Manager"),
          React.createElement(TabsTrigger, { value: "graph" }, "🕸️ Graph"),
          React.createElement(TabsTrigger, { value: "memories" }, "🧠 Memories"),
          React.createElement(TabsTrigger, { value: "skills" }, "🔧 Skills"),
          React.createElement(TabsTrigger, { value: "reflections" }, "💭 Reflections")
        ),

        // Memory Manager tab (NEW)
        activeTab === "manager" && React.createElement("div", { className: "mt-4" },
          React.createElement(MemoryManager, { memories, zones, onRefresh: refresh, onMutate: refreshAll })
        ),

        // Graph tab
        activeTab === "graph" && React.createElement("div", { className: "mt-4" },
          React.createElement(MemoryGraph, { nodes: graph.nodes, edges: graph.edges })
        ),

        // Memories tab (read-only list)
        activeTab === "memories" && React.createElement("div", { className: "mt-4 space-y-2" },
          graph.nodes.filter(n => n.type === "memory").map(m =>
            React.createElement(Card, { key: m.id },
              React.createElement(CardContent, { className: "py-3" },
                React.createElement("div", { className: "flex items-center gap-2 mb-1" },
                  m.pinned && React.createElement(Badge, { variant: "default" }, "📌"),
                  React.createElement(Badge, { variant: "outline" }, m.confidence),
                  React.createElement("span", { className: "text-xs text-muted-foreground" }, m.scope)
                ),
                React.createElement("p", { className: "text-sm" }, m.label)
              )
            )
          )
        ),

        // Skills tab
        activeTab === "skills" && React.createElement("div", { className: "mt-4 space-y-2" },
          graph.nodes.filter(n => n.type === "skill").map(s =>
            React.createElement(Card, { key: s.id },
              React.createElement(CardContent, { className: "py-3" },
                React.createElement("div", { className: "font-medium" }, s.id),
                React.createElement("p", { className: "text-sm text-muted-foreground" }, s.description),
                React.createElement("div", { className: "flex gap-1 mt-1 flex-wrap" },
                  s.triggers && s.triggers.map(t =>
                    React.createElement(Badge, { key: t, variant: "secondary", className: "text-xs" }, t)
                  )
                )
              )
            )
          )
        ),

        // Reflections tab
        activeTab === "reflections" && React.createElement("div", { className: "mt-4 space-y-2" },
          reflections.length === 0 && React.createElement("p", { className: "text-muted-foreground" }, "No reflections yet."),
          reflections.map((r, i) =>
            React.createElement(Card, { key: i },
              React.createElement(CardContent, { className: "py-3" },
                React.createElement("div", { className: "flex items-center gap-2 mb-1" },
                  React.createElement(Badge, { variant: "outline" }, r.mode || "unknown"),
                  React.createElement("span", { className: "text-xs text-muted-foreground" }, r.timestamp)
                ),
                React.createElement("p", { className: "text-sm" }, r.summary || "No summary")
              )
            )
          )
        )
      )
    );
  }

  window.__HERMES_PLUGINS__.register("mem-reflection-hermes", MemoryGraphPage);
})();
