from typing import Dict, List


class QueryRouter:
    """
    Anti-forgetting engine.
    Converts a natural-language query into a relevant subgraph slice,
    then serialises it as injectable AI context.
    """

    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder

    def get_subgraph(self, query: str, top_k: int = 10) -> Dict:
        query_emb = self.embedder.embed(query)
        similar = self.store.find_similar_nodes(query_emb, top_k=top_k)

        if not similar:
            return {"nodes": [], "edges": [], "context_text": ""}

        seed_ids = {node.id for _, node in similar}

        # Expand one hop of neighbors
        expanded = set(seed_ids)
        for nid in seed_ids:
            if nid in self.store.G:
                for neighbor in list(self.store.G.neighbors(nid))[:3]:
                    expanded.add(neighbor)

        relevant_edges = [
            e for e in self.store.get_all_edges()
            if e.source in expanded and e.target in expanded
        ]
        relevant_nodes = [
            self.store.get_node(nid) for nid in expanded
            if self.store.get_node(nid) is not None
        ]

        context_lines = []
        for score, node in similar[:6]:
            tag = f"[{node.type.upper()} | {node.source}]"
            context_lines.append(f"{tag} {node.text}")

        return {
            "nodes": [n.model_dump() for n in relevant_nodes if n],
            "edges": [e.model_dump() for e in relevant_edges],
            "context_text": "\n".join(context_lines),
            "scores": {node.id: round(score, 4) for score, node in similar},
        }

    def build_prompt(self, query: str, top_k: int = 8) -> str:
        sub = self.get_subgraph(query, top_k)
        ctx = sub.get("context_text", "")
        if not ctx:
            return query
        return (
            "You have access to a knowledge graph built from prior AI conversations.\n"
            "Relevant context:\n"
            f"{ctx}\n\n"
            f"User query: {query}"
        )
