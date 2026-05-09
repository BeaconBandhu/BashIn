from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        print(f"[Embedder] Loading model: {model_name}")
        self.model = SentenceTransformer(model_name)
        print("[Embedder] Ready")

    def embed(self, text: str) -> List[float]:
        return self.model.encode(text, convert_to_numpy=True).tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return self.model.encode(texts, convert_to_numpy=True).tolist()

    def cosine_similarity(self, a: List[float], b: List[float]) -> float:
        va = np.array(a)
        vb = np.array(b)
        denom = np.linalg.norm(va) * np.linalg.norm(vb)
        if denom < 1e-8:
            return 0.0
        return float(np.dot(va, vb) / denom)
