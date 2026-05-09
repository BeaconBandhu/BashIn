import { useState, useEffect, useCallback } from "react";

const API = "http://localhost:8000";

export function useProjects() {
  const [projects, setProjects] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API}/projects`);
      if (res.ok) setProjects(await res.json());
    } catch (_) {}
  }, []);

  const create = useCallback(async (name, description = "") => {
    const res = await fetch(`${API}/projects`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ name, description }),
    });
    if (!res.ok) throw new Error(await res.text());
    const p = await res.json();
    await refresh();
    return p;
  }, [refresh]);

  const remove = useCallback(async (projectId) => {
    await fetch(`${API}/projects/${projectId}`, { method: "DELETE" });
    await refresh();
  }, [refresh]);

  useEffect(() => { refresh(); }, [refresh]);

  return { projects, refresh: refresh, create, remove };
}


export function useGraphData(projectId, pollMs = 120000) {
  const [data,    setData]    = useState(null);
  const [stats,   setStats]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  const fetchGraph = useCallback(async () => {
    if (!projectId) return;
    try {
      const res = await fetch(`${API}/projects/${projectId}/graph`);
      if (!res.ok) throw new Error(res.statusText);
      setData(await res.json());
    } catch (e) { setError(e.message); }
  }, [projectId]);

  const fetchStats = useCallback(async () => {
    if (!projectId) return;
    try {
      const res = await fetch(`${API}/projects/${projectId}/stats`);
      if (res.ok) setStats(await res.json());
    } catch (_) {}
  }, [projectId]);

  useEffect(() => {
    setData(null);
    setStats(null);
    if (!projectId) return;
    fetchGraph();
    fetchStats();
    const id = setInterval(() => { fetchGraph(); fetchStats(); }, pollMs);
    return () => clearInterval(id);
  }, [projectId, fetchGraph, fetchStats, pollMs]);

  // ingest text
  const ingest = useCallback(async ({ content, source, sessionId }) => {
    if (!projectId) throw new Error("No project selected");
    setLoading(true);
    try {
      const res = await fetch(`${API}/projects/${projectId}/ingest`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ content, source, session_id: sessionId }),
      });
      if (!res.ok) throw new Error(await res.text());
      const result = await res.json();
      await fetchGraph();
      await fetchStats();
      return result;
    } finally { setLoading(false); }
  }, [projectId, fetchGraph, fetchStats]);

  // ingest multiple files
  const ingestFiles = useCallback(async (fileEntries) => {
    // fileEntries: [{ file: File, source: string }, ...]
    if (!projectId) throw new Error("No project selected");
    setLoading(true);
    try {
      const form = new FormData();
      const sources = [];
      for (const { file, source } of fileEntries) {
        form.append("files", file);
        sources.push(source);
      }
      form.append("sources", JSON.stringify(sources));
      const res = await fetch(`${API}/projects/${projectId}/ingest/files`, {
        method: "POST",
        body:   form,
      });
      if (!res.ok) throw new Error(await res.text());
      const result = await res.json();
      await fetchGraph();
      await fetchStats();
      return result;
    } finally { setLoading(false); }
  }, [projectId, fetchGraph, fetchStats]);

  // semantic query
  const query = useCallback(async (queryText, topK = 10) => {
    if (!projectId) return null;
    const res = await fetch(`${API}/projects/${projectId}/query`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ query: queryText, top_k: topK }),
    });
    return res.json();
  }, [projectId]);

  // download export .md
  const exportMd = useCallback(async (projectName) => {
    if (!projectId) return;
    const res = await fetch(`${API}/projects/${projectId}/export`);
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `${projectName || projectId}_knowledge.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [projectId]);

  return { data, stats, loading, error, ingest, ingestFiles, query, exportMd, refresh: fetchGraph };
}
