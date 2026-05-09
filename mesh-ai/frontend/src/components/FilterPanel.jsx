const NODE_TYPES   = ["memory", "garbage", "string", "tree", "branch"];
const AI_SOURCES   = ["chatgpt", "claude", "gemini", "codex", "markdown", "unknown"];

const TYPE_LABELS = {
  memory:   "Memory",
  garbage:  "Garbage",
  string:   "String",
  tree:     "Tree",
  branch:   "Branch",
};

const SOURCE_LABELS = {
  chatgpt:  "🤖 ChatGPT",
  claude:   "🔷 Claude",
  gemini:   "💎 Gemini",
  codex:    "⚡ Codex",
  markdown: "📄 Markdown",
  unknown:  "❓ Unknown",
};

export default function FilterPanel({ filters, onFilterChange }) {
  const toggleType = (type) => {
    const next = filters.types.includes(type)
      ? filters.types.filter((t) => t !== type)
      : [...filters.types, type];
    onFilterChange({ ...filters, types: next });
  };

  const toggleSource = (src) => {
    const next = filters.sources.includes(src)
      ? filters.sources.filter((s) => s !== src)
      : [...filters.sources, src];
    onFilterChange({ ...filters, sources: next });
  };

  const allTypes   = filters.types.length   === NODE_TYPES.length;
  const allSources = filters.sources.length === AI_SOURCES.length;

  return (
    <div className="filter-panel">
      <h3>Filters</h3>

      <div className="filter-section">
        <label>
          Node Type &nbsp;
          <span
            style={{ cursor: "pointer", color: "var(--accent)", fontSize: 11 }}
            onClick={() =>
              onFilterChange({ ...filters, types: allTypes ? [] : [...NODE_TYPES] })
            }
          >
            {allTypes ? "hide all" : "show all"}
          </span>
        </label>
        <div className="filter-chips">
          {NODE_TYPES.map((type) => (
            <span
              key={type}
              className={`filter-chip ${type} ${filters.types.includes(type) ? "active" : ""}`}
              onClick={() => toggleType(type)}
            >
              {TYPE_LABELS[type]}
            </span>
          ))}
        </div>
      </div>

      <div className="filter-section">
        <label>
          AI Source &nbsp;
          <span
            style={{ cursor: "pointer", color: "var(--accent)", fontSize: 11 }}
            onClick={() =>
              onFilterChange({ ...filters, sources: allSources ? [] : [...AI_SOURCES] })
            }
          >
            {allSources ? "hide all" : "show all"}
          </span>
        </label>
        <div className="filter-chips">
          {AI_SOURCES.map((src) => (
            <span
              key={src}
              className={`filter-chip source ${filters.sources.includes(src) ? "active" : ""}`}
              onClick={() => toggleSource(src)}
            >
              {SOURCE_LABELS[src]}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
