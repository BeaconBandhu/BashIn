import { useState, useCallback } from "react";
import SphereGraph  from "./components/SphereGraph";
import NodePanel    from "./components/NodePanel";
import FilterPanel  from "./components/FilterPanel";
import IngestPanel  from "./components/IngestPanel";
import StatsBar     from "./components/StatsBar";
import ProjectPanel from "./components/ProjectPanel";
import { useProjects, useGraphData } from "./hooks/useGraphData";

const ALL_TYPES   = ["memory", "garbage", "string", "tree", "branch"];
const ALL_SOURCES = ["chatgpt", "claude", "gemini", "codex", "markdown", "unknown"];

function QuickCreateProject({ onCreate }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err,  setErr]  = useState("");

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true); setErr("");
    try { await onCreate(name.trim()); }
    catch (e) { setErr(e.message || "Could not create project — is the backend running?"); }
    finally { setBusy(false); }
  };

  return (
    <div className="quick-create">
      <input
        className="quick-input"
        placeholder="Project name…"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        autoFocus
      />
      <button className="btn-ingest" onClick={submit} disabled={busy || !name.trim()}>
        {busy ? "Creating…" : "Create Project →"}
      </button>
      {err && <span className="quick-err">{err}</span>}
    </div>
  );
}

export default function App() {
  const { projects, refresh: refreshProjects, create: createProject, remove: deleteProject } = useProjects();

  const [activeProject, setActiveProject] = useState(null);
  const [selected,      setSelected]      = useState(null);
  const [showIngest,    setShowIngest]    = useState(false);
  const [noProjectWarn, setNoProjectWarn] = useState(false);
  const [queryText,     setQueryText]     = useState("");
  const [queryResult,   setQueryResult]   = useState(null);
  const [filters, setFilters] = useState({ types: [...ALL_TYPES], sources: [...ALL_SOURCES] });

  const {
    data, stats, loading,
    ingest, ingestFiles, query, exportMd,
  } = useGraphData(activeProject?.id, 120000);

  const handleSelectProject = useCallback((p) => {
    setActiveProject(p);
    setSelected(null);
    setQueryResult(null);
  }, []);

  const handleCreateProject = useCallback(async (name, desc) => {
    const p = await createProject(name, desc);
    setActiveProject(p);
    setSelected(null);
  }, [createProject]);

  const handleDeleteProject = useCallback(async (id) => {
    await deleteProject(id);
    if (activeProject?.id === id) {
      setActiveProject(null);
      setSelected(null);
    }
    await refreshProjects();
  }, [deleteProject, activeProject, refreshProjects]);

  const handleExport = useCallback((p) => {
    exportMd(p.name);
  }, [exportMd]);

  const handleQuery = async () => {
    if (!queryText.trim() || !activeProject) return;
    const res = await query(queryText);
    setQueryResult(res);
  };

  const isEmpty = !data?.nodes?.length;

  return (
    <div className="app">
      {/* ── header ─────────────────────────────────────────────────────── */}
      <header className="header">
        <div className="logo">
          <span className="logo-icon">◈</span>
          <span className="logo-text">MeshAI</span>
          <span className="logo-sub">Multi-AI Knowledge Mesh</span>
        </div>

        {activeProject && <StatsBar stats={stats} />}

        <div style={{ display: "flex", gap: 8, marginLeft: "auto", alignItems: "center" }}>
          {activeProject && (
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
              Project: <strong style={{ color: "var(--accent)" }}>{activeProject.name}</strong>
            </span>
          )}
          <button
            className="btn-ingest"
            onClick={() => {
              if (!activeProject) { setNoProjectWarn(true); setTimeout(() => setNoProjectWarn(false), 3000); return; }
              setShowIngest(true);
            }}
          >
            + Ingest
          </button>
        </div>
      </header>

      {/* ── main ───────────────────────────────────────────────────────── */}
      <main className="main">
        {/* ── sidebar ──────────────────────────────────────────────────── */}
        <aside className="sidebar">
          <ProjectPanel
            projects={projects}
            activeProject={activeProject}
            onSelect={handleSelectProject}
            onCreate={handleCreateProject}
            onDelete={handleDeleteProject}
            onExport={handleExport}
          />

          {activeProject && (
            <>
              <FilterPanel filters={filters} onFilterChange={setFilters} />
              {selected && (
                <NodePanel node={selected} onClose={() => setSelected(null)} />
              )}
              {queryResult && !selected && (
                <div className="node-panel">
                  <h3>Search Result</h3>
                  <div className="node-text" style={{ fontSize: 12 }}>
                    {queryResult.context_text || "No matching nodes found."}
                  </div>
                  <button style={{ marginTop: 8, fontSize: 11 }} onClick={() => setQueryResult(null)}>
                    Dismiss
                  </button>
                </div>
              )}
            </>
          )}
        </aside>

        {/* ── graph area ───────────────────────────────────────────────── */}
        <div className="graph-container">
          {loading && <div className="loading-overlay">Processing…</div>}

          {noProjectWarn && (
            <div className="toast-warn">Create or select a project first (see the sidebar ←)</div>
          )}

          {!activeProject ? (
            <div className="empty-state">
              <div className="empty-icon">◈</div>
              <p>Create a project to get started.</p>
              <QuickCreateProject onCreate={handleCreateProject} />
            </div>
          ) : isEmpty ? (
            <div className="empty-state">
              <div className="empty-icon">◈</div>
              <p>No nodes yet. Ingest a conversation to begin.</p>
              <button onClick={() => setShowIngest(true)}>+ Ingest Conversation</button>
            </div>
          ) : (
            <SphereGraph data={data} filters={filters} onNodeClick={setSelected} />
          )}

          {activeProject && !isEmpty && (
            <div className="query-bar">
              <input
                type="text"
                placeholder="Search the knowledge graph…"
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleQuery()}
              />
              <button onClick={handleQuery}>Search</button>
            </div>
          )}
        </div>
      </main>

      {showIngest && (
        <IngestPanel
          onIngest={ingest}
          onIngestFiles={ingestFiles}
          onClose={() => setShowIngest(false)}
        />
      )}
    </div>
  );
}
