import { useState, useRef } from "react";

const SOURCES = [
  { value: "chatgpt",  label: "🤖 ChatGPT"  },
  { value: "claude",   label: "🔷 Claude"   },
  { value: "gemini",   label: "💎 Gemini"   },
  { value: "codex",    label: "⚡ Codex"    },
  { value: "markdown", label: "📄 Other"    },
];

const emptyEntry = () => ({ id: crypto.randomUUID(), file: null, source: "markdown", text: "" });

export default function IngestPanel({ onIngest, onIngestFiles, onClose }) {
  const [entries,  setEntries]  = useState([emptyEntry()]);
  const [pasteText, setPasteText] = useState("");
  const [pasteSource, setPasteSource] = useState("claude");
  const [tab,      setTab]      = useState("files"); // "files" | "paste"
  const [status,   setStatus]   = useState(null);
  const [loading,  setLoading]  = useState(false);
  const fileRefs = useRef({});

  // ── entry helpers ──────────────────────────────────────────────────────────
  const addEntry = () => setEntries((e) => [...e, emptyEntry()]);

  const removeEntry = (id) =>
    setEntries((e) => e.length > 1 ? e.filter((x) => x.id !== id) : e);

  const setEntryFile = (id, file) =>
    setEntries((e) => e.map((x) => x.id === id ? { ...x, file } : x));

  const setEntrySource = (id, source) =>
    setEntries((e) => e.map((x) => x.id === id ? { ...x, source } : x));

  const handleDrop = (id, ev) => {
    ev.preventDefault();
    const file = ev.dataTransfer.files[0];
    if (file) setEntryFile(id, file);
  };

  // ── submit ─────────────────────────────────────────────────────────────────
  const handleSubmit = async () => {
    setLoading(true);
    setStatus(null);
    try {
      if (tab === "files") {
        const filled = entries.filter((e) => e.file);
        if (!filled.length) { setStatus("✗ No files selected."); return; }
        const result = await onIngestFiles(filled.map((e) => ({ file: e.file, source: e.source })));
        const total  = result.results.reduce((s, r) => s + r.nodes, 0);
        setStatus(`✓ ${result.files} file(s) → ${total} nodes added`);
        setEntries([emptyEntry()]);
      } else {
        if (!pasteText.trim()) { setStatus("✗ Nothing to ingest."); return; }
        const result = await onIngest({ content: pasteText, source: pasteSource, sessionId: crypto.randomUUID() });
        setStatus(`✓ ${result.chunks} chunks → ${result.nodes_added} nodes added`);
        setPasteText("");
      }
    } catch (e) {
      setStatus(`✗ ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const canSubmit = !loading && (
    tab === "files" ? entries.some((e) => e.file) : pasteText.trim().length > 0
  );

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: 620 }}>
        <h2>
          Ingest Conversation
          <button className="modal-close" onClick={onClose}>✕</button>
        </h2>

        {/* tab switcher */}
        <div className="ingest-tabs">
          <button className={`ingest-tab ${tab === "files" ? "active" : ""}`} onClick={() => setTab("files")}>
            📁 Upload Files
          </button>
          <button className={`ingest-tab ${tab === "paste" ? "active" : ""}`} onClick={() => setTab("paste")}>
            📋 Paste Text
          </button>
        </div>

        {/* ── files tab ──────────────────────────────────────────────── */}
        {tab === "files" && (
          <div className="file-entries">
            {entries.map((entry, idx) => (
              <div key={entry.id} className="file-entry">
                <span className="entry-num">{idx + 1}</span>

                <div
                  className={`file-drop-sm ${entry.file ? "has-file" : ""}`}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => handleDrop(entry.id, e)}
                  onClick={() => fileRefs.current[entry.id]?.click()}
                >
                  {entry.file ? `✓ ${entry.file.name}` : "Drop .md or click"}
                  <input
                    ref={(el) => (fileRefs.current[entry.id] = el)}
                    type="file" accept=".md,.txt"
                    style={{ display: "none" }}
                    onChange={(e) => e.target.files[0] && setEntryFile(entry.id, e.target.files[0])}
                  />
                </div>

                <select
                  className="entry-source"
                  value={entry.source}
                  onChange={(e) => setEntrySource(entry.id, e.target.value)}
                >
                  {SOURCES.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>

                <button
                  className="entry-remove"
                  onClick={() => removeEntry(entry.id)}
                  disabled={entries.length === 1}
                >✕</button>
              </div>
            ))}

            <button className="add-file-btn" onClick={addEntry}>
              + Add Another File
            </button>
          </div>
        )}

        {/* ── paste tab ──────────────────────────────────────────────── */}
        {tab === "paste" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <label>AI Source</label>
              <select value={pasteSource} onChange={(e) => setPasteSource(e.target.value)}>
                {SOURCES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label>Paste conversation text</label>
              <textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder={`User: How does React work?\nAssistant: React is a library for…`}
                style={{ minHeight: 180 }}
              />
            </div>
          </div>
        )}

        {status && (
          <div className={`ingest-status ${status.startsWith("✓") ? "ok" : "err"}`}>
            {status}
          </div>
        )}

        <div className="modal-actions">
          <button onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleSubmit} disabled={!canSubmit}>
            {loading ? "Processing…" : "Ingest →"}
          </button>
        </div>
      </div>
    </div>
  );
}
