import math
import networkx as nx
from typing import Dict, Tuple

try:
    import community as community_louvain
    HAS_LOUVAIN = True
except ImportError:
    HAS_LOUVAIN = False


class GraphAlgorithms:
    def __init__(self, store):
        self.store = store

    # ── betweenness centrality ────────────────────────────────────────────────

    def compute_betweenness(self) -> Dict[str, float]:
        G = self.store.G
        if len(G.nodes) < 2:
            return {}
        bc = nx.betweenness_centrality(G, weight="weight", normalized=True)
        max_bc = max(bc.values()) if bc else 1.0
        for node_id, score in bc.items():
            self.store.update_node(node_id, importance=score / (max_bc + 1e-8))
        return bc

    # ── louvain community detection ───────────────────────────────────────────

    def compute_communities(self) -> Dict[str, int]:
        if not HAS_LOUVAIN or len(self.store.G.nodes) < 2:
            return {}
        partition = community_louvain.best_partition(self.store.G, weight="weight")
        for node_id, comm in partition.items():
            self.store.update_node(node_id, community=comm)
        return partition

    # ── spherical positioning ─────────────────────────────────────────────────

    def compute_sphere_positions(self) -> Dict[str, Tuple[float, float, float]]:
        """
        Fibonacci (golden-ratio) sphere distribution.
        Radius = 1.0 - (importance * 0.7)
        High-importance nodes sink toward the core; new/rare nodes sit on the surface.
        """
        nodes = self.store.get_all_nodes()
        if not nodes:
            return {}

        n = len(nodes)
        golden = (1 + math.sqrt(5)) / 2
        positions: Dict[str, Tuple[float, float, float]] = {}

        for idx, node in enumerate(nodes):
            radius = 1.0 - (node.importance * 0.7)   # [0.3, 1.0]

            theta = 2 * math.pi * idx / golden
            phi = math.acos(1 - 2 * (idx + 0.5) / n)

            x = radius * math.sin(phi) * math.cos(theta)
            y = radius * math.sin(phi) * math.sin(theta)
            z = radius * math.cos(phi)

            positions[node.id] = (x, y, z)
            self.store.update_node(node.id, x=x, y=y, z=z, depth=radius)

        return positions

    # ── run all ───────────────────────────────────────────────────────────────

    def compute_all(self):
        self.compute_betweenness()
        self.compute_communities()
        self.compute_sphere_positions()
