import re
import uuid
from typing import List, Optional
from datetime import datetime
from models.schemas import ConversationChunk, AISource


SOURCE_PATTERNS = {
    AISource.CHATGPT: re.compile(r"chatgpt|gpt[-\s]?[34o]|openai", re.IGNORECASE),
    AISource.CLAUDE:  re.compile(r"claude|anthropic",               re.IGNORECASE),
    AISource.GEMINI:  re.compile(r"gemini|google\s*ai|bard",        re.IGNORECASE),
    AISource.CODEX:   re.compile(r"codex|copilot",                  re.IGNORECASE),
}

USER_RE      = re.compile(r"^(?:User|Human|You)\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)
ASSISTANT_RE = re.compile(r"^(?:Assistant|AI|Claude|ChatGPT|Gemini|Gemini|Bot|Codex)\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)
TURN_SPLIT   = re.compile(r"\n(?=(?:User|Human|You|Assistant|AI|Claude|ChatGPT|Gemini|Bot|Codex)\s*:)", re.IGNORECASE)


class MarkdownImporter:
    def detect_source(self, text: str) -> AISource:
        for source, pattern in SOURCE_PATTERNS.items():
            if pattern.search(text[:500]):          # check the header
                return source
        return AISource.MARKDOWN

    def parse(self, content: str, session_id: Optional[str] = None) -> List[ConversationChunk]:
        session_id = session_id or str(uuid.uuid4())
        source = self.detect_source(content)

        structured = self._parse_turns(content, session_id, source)
        if structured:
            return structured
        return self._parse_paragraphs(content, session_id, source)

    # ── structured turn parser ────────────────────────────────────────────────

    def _parse_turns(
        self, content: str, session_id: str, source: AISource
    ) -> List[ConversationChunk]:
        turns = TURN_SPLIT.split(content)
        chunks: List[ConversationChunk] = []

        for turn in turns:
            turn = turn.strip()
            if not turn:
                continue
            m = USER_RE.match(turn)
            if m:
                role, text = "user", m.group(1).strip()
            else:
                m = ASSISTANT_RE.match(turn)
                if m:
                    role, text = "assistant", m.group(1).strip()
                else:
                    continue
            if len(text) < 15:
                continue
            chunks.append(ConversationChunk(
                id=str(uuid.uuid4()),
                text=text,
                source=source,
                role=role,
                session_id=session_id,
                timestamp=datetime.now(),
            ))

        return chunks

    # ── paragraph fallback ────────────────────────────────────────────────────

    def _parse_paragraphs(
        self, content: str, session_id: str, source: AISource
    ) -> List[ConversationChunk]:
        paragraphs = re.split(r"\n{2,}", content)
        chunks: List[ConversationChunk] = []
        for para in paragraphs:
            para = para.strip()
            if len(para) < 40:
                continue
            chunks.append(ConversationChunk(
                id=str(uuid.uuid4()),
                text=para,
                source=source,
                role="assistant",
                session_id=session_id,
                timestamp=datetime.now(),
            ))
        return chunks
