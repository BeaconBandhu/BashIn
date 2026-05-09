import { useRef, useEffect, useMemo, useCallback } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";

export const NODE_COLORS = {
  memory:  "#4a90d9",
  garbage: "#e74c3c",
  string:  "#f1c40f",
  tree:    "#2ecc71",
  branch:  "#9b59b6",
};

const NODE_DESC = {
  memory:  "Verified knowledge",
  garbage: "Incorrect / hallucinated",
  string:  "Shared across AIs",
  tree:    "New concept",
  branch:  "Cross-AI synthesis",
};

const SOURCE_EMOJI = {
  chatgpt:  "🤖",
  claude:   "🔷",
  gemini:   "💎",
  codex:    "⚡",
  markdown: "📄",
  unknown:  "❓",
};

export default function SphereGraph({ data, filters, onNodeClick }) {
  const fgRef = useRef();

  // ── filter data ────────────────────────────────────────────────────────────
  const graphData = useMemo(() => {
    if (!data?.nodes) return { nodes: [], links: [] };

    const activeTypes   = new Set(filters.types);
    const activeSources = new Set(filters.sources);

    const nodes = data.nodes.filter(
      (n) => activeTypes.has(n.type) && activeSources.has(n.source)
    );
    const nodeSet = new Set(nodes.map((n) => n.id));
    const links = (data.edges || [])
      .filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, value: e.weight || 1 }));

    return { nodes, links };
  }, [data, filters]);

  // ── node size ──────────────────────────────────────────────────────────────
  const nodeVal = useCallback(
    (node) => 1 + (node.importance || 0) * 12,
    []
  );

  // ── custom Three.js node object ────────────────────────────────────────────
  const nodeThreeObject = useCallback((node) => {
    const color = NODE_COLORS[node.type] || "#888";
    const size  = 0.8 + (node.importance || 0) * 6;

    const group = new THREE.Group();

    // core sphere
    const geo  = new THREE.SphereGeometry(size, 16, 16);
    const mat  = new THREE.MeshPhongMaterial({
      color,
      emissive:          color,
      emissiveIntensity: 0.25,
      transparent:       true,
      opacity:           0.88,
    });
    group.add(new THREE.Mesh(geo, mat));

    // glow shell for Branch nodes
    if (node.type === "branch") {
      const glowGeo = new THREE.SphereGeometry(size * 1.5, 16, 16);
      const glowMat = new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0.12,
      });
      group.add(new THREE.Mesh(glowGeo, glowMat));
    }

    // pulsing ring for String nodes (cross-AI shared)
    if (node.type === "string") {
      const ringGeo = new THREE.TorusGeometry(size * 1.4, size * 0.15, 8, 24);
      const ringMat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.5 });
      group.add(new THREE.Mesh(ringGeo, ringMat));
    }

    return group;
  }, []);

  // ── scene setup (sphere wireframe + lights) ────────────────────────────────
  useEffect(() => {
    if (!fgRef.current) return;
    const scene = fgRef.current.scene();

    // outer wireframe sphere — shows the "globe"
    const sphereGeo = new THREE.SphereGeometry(200, 36, 36);
    const sphereMat = new THREE.MeshBasicMaterial({
      color: "#1a1a3a", wireframe: true, transparent: true, opacity: 0.06,
    });
    const globeMesh = new THREE.Mesh(sphereGeo, sphereMat);
    scene.add(globeMesh);

    // inner layer sphere (mid radius) — shows depth layers
    const innerGeo = new THREE.SphereGeometry(120, 24, 24);
    const innerMat = new THREE.MeshBasicMaterial({
      color: "#2a1a4a", wireframe: true, transparent: true, opacity: 0.04,
    });
    scene.add(new THREE.Mesh(innerGeo, innerMat));

    const ambient = new THREE.AmbientLight(0x404060, 1.2);
    scene.add(ambient);

    const point1 = new THREE.PointLight(0x8888ff, 2, 600);
    point1.position.set(200, 200, 200);
    scene.add(point1);

    const point2 = new THREE.PointLight(0xff8844, 1, 400);
    point2.position.set(-200, -100, -200);
    scene.add(point2);

    return () => {
      scene.remove(globeMesh);
      scene.remove(ambient);
      scene.remove(point1);
      scene.remove(point2);
    };
  }, []);

  // ── auto-rotate when idle ──────────────────────────────────────────────────
  useEffect(() => {
    if (!fgRef.current) return;
    const ctrl = fgRef.current.controls();
    ctrl.autoRotate      = true;
    ctrl.autoRotateSpeed = 0.4;
  }, [graphData]);

  // ── tooltip HTML ───────────────────────────────────────────────────────────
  const nodeLabel = useCallback((node) => {
    const color = NODE_COLORS[node.type] || "#888";
    const emoji = SOURCE_EMOJI[node.source] || "❓";
    return `
      <div style="
        background:#0e0e1c; padding:10px 14px; border-radius:8px;
        border:1px solid ${color}; max-width:260px; font-family:sans-serif;
      ">
        <div style="color:${color}; font-weight:700; font-size:12px; margin-bottom:4px;">
          ${node.type.toUpperCase()} · ${NODE_DESC[node.type] || ""}
        </div>
        <div style="color:#888; font-size:11px; margin-bottom:6px;">
          ${emoji} ${node.source}
          &nbsp;|&nbsp; importance: ${(node.importance || 0).toFixed(3)}
        </div>
        <div style="color:#d0d0f0; font-size:12px; line-height:1.5;">
          ${node.label || ""}
        </div>
      </div>`;
  }, []);

  return (
    <ForceGraph3D
      ref={fgRef}
      graphData={graphData}
      nodeId="id"
      nodeVal={nodeVal}
      nodeThreeObject={nodeThreeObject}
      nodeThreeObjectExtend={false}
      nodeLabel={nodeLabel}
      onNodeClick={onNodeClick}
      linkSource="source"
      linkTarget="target"
      linkColor={() => "#2a2a50"}
      linkOpacity={0.5}
      linkWidth={(link) => Math.sqrt(link.value || 1) * 0.4}
      backgroundColor="#070710"
      showNavInfo={false}
      enableNavigationControls
      d3AlphaDecay={0.02}
      d3VelocityDecay={0.3}
    />
  );
}
