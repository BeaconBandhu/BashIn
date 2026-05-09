import re
from typing import List
from models.schemas import NodeType, AISource, ConversationChunk


GARBAGE_PATTERNS = [
    r"that\s+(?:is|'s)\s+(?:wrong|incorrect|not right|not accurate)",
    r"\b(?:incorrect|mistake|error)\b",
    r"actually[,\s]+(?:that|it|the)\b",
    r"let me correct\b",
    r"\bi\s+(?:made a mistake|was wrong|apologize)\b",
    r"\bmy\s+(?:mistake|error|bad)\b",
    r"\bi(?:'m| am)\s+(?:sorry|incorrect|wrong)\b",
    r"\bthat(?:'s| is)\s+not\s+(?:right|correct|accurate)\b",
    r"\b(?:hallucin|fabricat|made up)\b",
    r"\bi\s+(?:don't|do not)\s+(?:actually\s+)?know\b",
    r"\bapolog(?:ize|ies)\b",
]

_GARBAGE_RE = [re.compile(p, re.IGNORECASE) for p in GARBAGE_PATTERNS]


class NodeClassifier:
    """
    Classifies a concept into one of: Memory, Garbage, String, Tree, Branch

    Memory  — verified, repeated concept from same source
    Garbage — contains self-correction or hallucination signal
    String  — same concept exists from a DIFFERENT AI source (cosine > string_threshold)
    Tree    — brand-new concept, no similar node exists (cosine < tree_threshold)
    Branch  — concept from different source, medium-high similarity (synthesized)
    """

    def __init__(
        self,
        embedder,
        string_threshold: float = 0.82,
        tree_threshold: float = 0.45,
        branch_threshold: float = 0.62,
    ):
        self.embedder = embedder
        self.string_threshold = string_threshold
        self.tree_threshold = tree_threshold
        self.branch_threshold = branch_threshold

    def is_garbage(self, text: str) -> bool:
        return any(p.search(text) for p in _GARBAGE_RE)

    def classify(
        self,
        concept: str,
        embedding: List[float],
        source: AISource,
        existing_nodes: List[dict],
    ) -> NodeType:
        if self.is_garbage(concept):
            return NodeType.GARBAGE

        if not existing_nodes:
            return NodeType.TREE

        best_sim = 0.0
        best_node = None
        for node in existing_nodes:
            if node.get("embedding"):
                sim = self.embedder.cosine_similarity(embedding, node["embedding"])
                if sim > best_sim:
                    best_sim = sim
                    best_node = node

        if best_sim >= self.string_threshold and best_node:
            if best_node.get("source") != source:
                return NodeType.STRING
            return NodeType.MEMORY

        if best_sim < self.tree_threshold:
            return NodeType.TREE

        # medium similarity zone
        if best_node and best_node.get("source") != source and best_sim >= self.branch_threshold:
            return NodeType.BRANCH

        return NodeType.MEMORY
