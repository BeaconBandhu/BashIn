import json
import re
from typing import List, Tuple, Optional


class TripleExtractor:
    """
    Extracts subject-predicate-object triples from text.
    Uses OpenAI if a key is provided; falls back to regex rules.
    """

    def __init__(self, openai_api_key: Optional[str] = None):
        self.client = None
        if openai_api_key:
            try:
                import openai
                self.client = openai.OpenAI(api_key=openai_api_key)
            except ImportError:
                pass

    def extract(self, text: str) -> List[Tuple[str, str, str]]:
        if self.client:
            return self._llm_extract(text)
        return self._regex_extract(text)

    def _llm_extract(self, text: str) -> List[Tuple[str, str, str]]:
        prompt = (
            "Extract 5–15 knowledge triples (subject, predicate, object) from this text.\n"
            "Return ONLY a JSON array: [[\"subject\",\"predicate\",\"object\"],...]\n"
            "Keep subjects/objects concise (1–4 words).\n\n"
            f"Text: {text[:1500]}\n\nJSON:"
        )
        try:
            resp = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=600,
            )
            raw = resp.choices[0].message.content.strip()
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                triples = json.loads(match.group())
                return [(t[0], t[1], t[2]) for t in triples if len(t) == 3]
        except Exception:
            pass
        return self._regex_extract(text)

    def _regex_extract(self, text: str) -> List[Tuple[str, str, str]]:
        patterns = [
            (r"(\w+(?:\s\w+)?)\s+is\s+(?:a\s+)?(\w+(?:\s\w+)?)", "is-a"),
            (r"(\w+(?:\s\w+)?)\s+uses?\s+(\w+(?:\s\w+)?)", "uses"),
            (r"(\w+(?:\s\w+)?)\s+has\s+(\w+(?:\s\w+)?)", "has"),
            (r"(\w+(?:\s\w+)?)\s+(?:can|should|will)\s+(\w+(?:\s\w+)?)", "can"),
            (r"(\w+(?:\s\w+)?)\s+(?:returns?|outputs?|produces?)\s+(\w+(?:\s\w+)?)", "returns"),
            (r"(\w+(?:\s\w+)?)\s+(?:requires?|needs?)\s+(\w+(?:\s\w+)?)", "requires"),
        ]
        triples: List[Tuple[str, str, str]] = []
        for pattern, relation in patterns:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                s, o = m.group(1).strip(), m.group(2).strip()
                if s and o and s.lower() != o.lower():
                    triples.append((s, relation, o))
        return triples[:15]
