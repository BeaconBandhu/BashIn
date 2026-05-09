import { NODE_COLORS } from "./SphereGraph";

const NODE_DESC = {
  memory:  "Verified knowledge repeated across sessions",
  garbage: "Incorrect or hallucinated answer — isolated, not deleted",
  string:  "Same concept found across different AI sources",
  tree:    "Brand-new concept introduced for the first time",
  branch:  "Cross-AI synthesis — merged from multiple AI sources",
};

export default function NodePanel({ node, onClose }) {
  if (!node) return null;
  const color = NODE_COLORS[node.type] || "#888";

  return (
    <div className="node-panel">
      <h3>
        Selected Node
        <button className="node-close" onClick={onClose}>✕</button>
      </h3>

      <span className={`node-badge ${node.type}`}>{node.type.toUpperCase()}</span>

      <p style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 8 }}>
        {NODE_DESC[node.type]}
      </p>

      <div className="node-source">
        Source: <strong style={{ color }}>{node.source}</strong>
        &nbsp;&nbsp;|&nbsp;&nbsp;
        Importance: <strong>{(node.importance || 0).toFixed(4)}</strong>
      </div>

      <div className="node-source">
        Community: <strong>{node.community ?? "—"}</strong>
        &nbsp;&nbsp;|&nbsp;&nbsp;
        Depth: <strong>{(node.depth || 1).toFixed(2)}</strong>
      </div>

      <div className="node-text">{node.text || node.label}</div>

      <div className="node-meta" style={{ marginTop: 10 }}>
        ID: <code style={{ fontSize: 10 }}>{node.id}</code>
      </div>
    </div>
  );
}
