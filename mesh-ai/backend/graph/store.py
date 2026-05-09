import networkx as nx
import numpy as np
import json
from typing import List, Optional, Dict, Tuple
from models.schemas import GraphNode, GraphEdge, GraphData


class GraphStore:
    def __init__(self):
        self.G: nx.Graph = nx.Graph()
        self._nodes: Dict[str, GraphNode] = {}

    # ── nodes ────────────────────────────────────────────────────────────────

    def add_node(self, node: GraphNode) -> str:
        self.G.add_node(node.id)
        self._nodes[node.id] = node
        return node.id

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> List[GraphNode]:
        return list(self._nodes.values())

    def update_node(self, node_id: str, **kwargs):
        node = self._nodes.get(node_id)
        if node:
            for k, v in kwargs.items():
                setattr(node, k, v)

    # ── edges ────────────────────────────────────────────────────────────────

    def add_edge(self, edge: GraphEdge):
        if edge.source not in self.G or edge.target not in self.G:
            return
        if self.G.has_edge(edge.source, edge.target):
            self.G[edge.source][edge.target]["weight"] += edge.weight
        else:
            self.G.add_edge(
                edge.source, edge.target,
                weight=edge.weight, relation=edge.relation,
            )

    def get_all_edges(self) -> List[GraphEdge]:
        return [
            GraphEdge(
                source=u, target=v,
                weight=d.get("weight", 1.0),
                relation=d.get("relation", ""),
            )
            for u, v, d in self.G.edges(data=True)
        ]

    # ── similarity search ────────────────────────────────────────────────────

    def find_similar_nodes(
        self, embedding: List[float], top_k: int = 10
    ) -> List[Tuple[float, GraphNode]]:
        if not embedding:
            return []
        query = np.array(embedding)
        scores = []
        for node in self._nodes.values():
            if node.embedding:
                vec = np.array(node.embedding)
                denom = np.linalg.norm(query) * np.linalg.norm(vec)
                if denom > 1e-8:
                    sim = float(np.dot(query, vec) / denom)
                    scores.append((sim, node))
        scores.sort(key=lambda x: -x[0])
        return scores[:top_k]

    # ── serialisation ────────────────────────────────────────────────────────

    def get_graph_data(self) -> GraphData:
        return GraphData(
            nodes=list(self._nodes.values()),
            edges=self.get_all_edges(),
        )

    def save(self, path: str):
        data = {
            "nodes": [n.model_dump() for n in self._nodes.values()],
            "edges": [e.model_dump() for e in self.get_all_edges()],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str, ensure_ascii=False)

    def load(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for nd in data.get("nodes", []):
            node = GraphNode(**nd)
            self.G.add_node(node.id)
            self._nodes[node.id] = node
        for ed in data.get("edges", []):
            edge = GraphEdge(**ed)
            self.G.add_edge(
                edge.source, edge.target,
                weight=edge.weight, relation=edge.relation,
            )

    def clear(self):
        self.G.clear()
        self._nodes.clear()
