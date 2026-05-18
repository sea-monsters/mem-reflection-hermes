(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Tabs, TabsList, TabsTrigger } = SDK.components;

  // Simple force-directed graph renderer using SVG
  function MemoryGraph({ nodes, edges }) {
    const svgRef = React.useRef(null);
    const [selected, setSelected] = React.useState(null);
    const width = 800;
    const height = 500;

    // Simple layout: memories on left, skills on right, with some jitter
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
        // Edges
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
        // Nodes
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
    return React.createElement("div", { className: "grid grid-cols-3 gap-4 mb-6" },
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

  function MemoryGraphPage() {
    const [graph, setGraph] = React.useState({ nodes: [], edges: [] });
    const [stats, setStats] = React.useState(null);
    const [reflections, setReflections] = React.useState([]);
    const [activeTab, setActiveTab] = React.useState("graph");

    React.useEffect(() => {
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/graph")
        .then(setGraph)
        .catch(console.error);
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/stats")
        .then(setStats)
        .catch(console.error);
      SDK.fetchJSON("/api/plugins/mem-reflection-hermes/reflections")
        .then(r => setReflections(r.reflections || []))
        .catch(console.error);
    }, []);

    return React.createElement("div", { className: "p-6 space-y-6" },
      React.createElement("h1", { className: "text-2xl font-bold" }, "Memory Graph"),
      React.createElement(StatsPanel, { stats }),
      React.createElement(Tabs, { value: activeTab, onValueChange: setActiveTab },
        React.createElement(TabsList, null,
          React.createElement(TabsTrigger, { value: "graph" }, "Graph"),
          React.createElement(TabsTrigger, { value: "memories" }, "Memories"),
          React.createElement(TabsTrigger, { value: "skills" }, "Skills"),
          React.createElement(TabsTrigger, { value: "reflections" }, "Reflections")
        ),
        // Graph tab
        activeTab === "graph" && React.createElement("div", { className: "mt-4" },
          React.createElement(MemoryGraph, { nodes: graph.nodes, edges: graph.edges })
        ),
        // Memories tab
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
