from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
from datetime import datetime


class NodeType(str, Enum):
    MEMORY = "memory"
    GARBAGE = "garbage"
    STRING = "string"
    TREE = "tree"
    BRANCH = "branch"


class AISource(str, Enum):
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GEMINI = "gemini"
    CODEX = "codex"
    MARKDOWN = "markdown"
    UNKNOWN = "unknown"


class ConversationChunk(BaseModel):
    id: str
    text: str
    source: AISource
    role: str  # "user" or "assistant"
    timestamp: Optional[datetime] = None
    session_id: str
    embedding: Optional[List[float]] = None


class GraphNode(BaseModel):
    id: str
    label: str
    type: NodeType
    source: AISource
    text: str
    embedding: Optional[List[float]] = None
    importance: float = 0.0       # betweenness centrality score
    depth: float = 1.0            # sphere radius: 1=surface, 0=core
    community: int = 0
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


class GraphEdge(BaseModel):
    source: str
    target: str
    weight: float = 1.0
    relation: str = ""


class GraphData(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class IngestRequest(BaseModel):
    source: AISource
    content: str
    session_id: str


class QueryRequest(BaseModel):
    query: str
    top_k: int = 10


class Project(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str
    node_count: int = 0


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
