export default function StatsBar({ stats }) {
  if (!stats) return null;

  const chips = [
    { key: "memory",  label: "Memory",  count: stats.by_type?.memory  || 0 },
    { key: "tree",    label: "Tree",    count: stats.by_type?.tree    || 0 },
    { key: "branch",  label: "Branch",  count: stats.by_type?.branch  || 0 },
    { key: "string",  label: "String",  count: stats.by_type?.string  || 0 },
    { key: "garbage", label: "Garbage", count: stats.by_type?.garbage || 0 },
  ];

  return (
    <div className="stats-bar">
      {chips.map(({ key, label, count }) => (
        <span key={key} className={`stat-chip ${key}`}>
          {label}: {count}
        </span>
      ))}
      <span style={{ fontSize: 12, color: "var(--text-dim)", alignSelf: "center" }}>
        {stats.total_edges || 0} edges
      </span>
    </div>
  );
}
