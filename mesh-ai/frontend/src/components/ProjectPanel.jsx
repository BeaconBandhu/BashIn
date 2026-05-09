import { useState } from "react";

export default function ProjectPanel({ projects, activeProject, onSelect, onCreate, onDelete, onExport }) {
  const [creating,  setCreating]  = useState(false);
  const [newName,   setNewName]   = useState("");
  const [newDesc,   setNewDesc]   = useState("");
  const [confirmId, setConfirmId] = useState(null);
  const [createErr, setCreateErr] = useState("");
  const [createBusy, setCreateBusy] = useState(false);

  const submit = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreateBusy(true); setCreateErr("");
    try {
      await onCreate(name, newDesc.trim());
      setNewName(""); setNewDesc(""); setCreating(false);
    } catch (e) {
      setCreateErr(e.message || "Failed — is the backend running?");
    } finally {
      setCreateBusy(false);
    }
  };

  return (
    <div className="project-panel">
      <div className="project-header">
        <span className="project-title">Projects</span>
        <button className="project-add-btn" onClick={() => setCreating((v) => !v)} title="New project">
          {creating ? "✕" : "+"}
        </button>
      </div>

      {creating && (
        <div className="project-create">
          <input
            autoFocus
            className="project-input"
            placeholder="Project name…"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <input
            className="project-input"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          {createErr && <span style={{ fontSize: 11, color: "var(--garbage)" }}>{createErr}</span>}
          <button className="project-create-btn" onClick={submit} disabled={!newName.trim() || createBusy}>
            {createBusy ? "Creating…" : "Create"}
          </button>
        </div>
      )}

      <div className="project-list">
        {projects.length === 0 && !creating && (
          <div className="project-empty">No projects yet — create one to begin.</div>
        )}

        {projects.map((p) => {
          const active = activeProject?.id === p.id;
          return (
            <div
              key={p.id}
              className={`project-item ${active ? "active" : ""}`}
              onClick={() => onSelect(p)}
            >
              <div className="project-item-main">
                <span className="project-dot" style={{ opacity: active ? 1 : 0.3 }}>◈</span>
                <span className="project-name">{p.name}</span>
                <span className="project-count">{p.node_count || 0}</span>
              </div>

              {active && (
                <div className="project-item-actions" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="proj-action-btn"
                    title="Export as .md"
                    onClick={() => onExport(p)}
                  >
                    ↓ export
                  </button>
                  {confirmId === p.id ? (
                    <>
                      <button
                        className="proj-action-btn danger"
                        onClick={() => { onDelete(p.id); setConfirmId(null); }}
                      >
                        confirm delete
                      </button>
                      <button className="proj-action-btn" onClick={() => setConfirmId(null)}>
                        cancel
                      </button>
                    </>
                  ) : (
                    <button
                      className="proj-action-btn"
                      title="Delete project"
                      onClick={() => setConfirmId(p.id)}
                    >
                      ✕ delete
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
