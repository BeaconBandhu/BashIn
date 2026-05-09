import re
from collections import Counter
from typing import List, Tuple
from itertools import combinations


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "this", "that", "was", "are",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall", "can",
    "not", "no", "yes", "i", "you", "he", "she", "we", "they", "me",
    "him", "her", "us", "them", "my", "your", "his", "its", "our", "their",
    "what", "which", "who", "when", "where", "why", "how", "all", "each",
    "every", "if", "then", "than", "so", "as", "up", "out", "about",
    "into", "also", "just", "more", "some", "any", "there", "here",
    "very", "much", "many", "such", "own", "same", "other", "new",
}


class NGramExtractor:
    def __init__(self, window_size: int = 4, min_freq: int = 1):
        self.window_size = window_size
        self.min_freq = min_freq

    def clean(self, text: str) -> List[str]:
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        return [t for t in text.split() if t not in STOPWORDS and len(t) > 2]

    def extract_concepts(self, text: str, top_n: int = 30) -> List[str]:
        tokens = self.clean(text)
        freq = Counter(tokens)
        return [w for w, _ in freq.most_common(top_n) if freq[w] >= self.min_freq]

    def extract_cooccurrences(self, text: str) -> List[Tuple[str, str, float]]:
        tokens = self.clean(text)
        co: Counter = Counter()
        for i in range(len(tokens)):
            window = tokens[i: i + self.window_size]
            for a, b in combinations(set(window), 2):
                co[tuple(sorted([a, b]))] += 1
        return [(a, b, float(c)) for (a, b), c in co.items() if c >= self.min_freq]
