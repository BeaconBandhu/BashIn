import uuid
from datetime import datetime
from typing import List, Optional

from models.schemas import GraphEdge, GraphNode, Project


class ProjectStore:
    def __init__(self, db):
        self.db = db

    async def create(self, name: str, description: str = "") -> Project:
        p = Project(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
            node_count=0,
        )
        await self.db.projects.insert_one(p.model_dump())
        return p

    async def get_all(self) -> List[Project]:
        return [Project(**p) async for p in self.db.projects.find({}, {"_id": 0})]

    async def get(self, project_id: str) -> Optional[Project]:
        p = await self.db.projects.find_one({"id": project_id}, {"_id": 0})
        return Project(**p) if p else None

    async def update_node_count(self, project_id: str, count: int):
        await self.db.projects.update_one(
            {"id": project_id}, {"$set": {"node_count": count}}
        )

    async def delete(self, project_id: str):
        await self.db.projects.delete_one({"id": project_id})
        await self.db.nodes.delete_many({"project_id": project_id})
        await self.db.edges.delete_many({"project_id": project_id})


# ── graph persistence helpers ─────────────────────────────────────────────────

async def save_graph(db, project_id: str, nodes: List[GraphNode], edges: List[GraphEdge]):
    if nodes:
        docs = [{"project_id": project_id, **n.model_dump()} for n in nodes]
        await db.nodes.delete_many({"project_id": project_id})
        await db.nodes.insert_many(docs)
    if edges:
        edge_docs = [{"project_id": project_id, **e.model_dump()} for e in edges]
        await db.edges.delete_many({"project_id": project_id})
        await db.edges.insert_many(edge_docs)


async def load_graph(db, project_id: str):
    nodes = [
        GraphNode(**n)
        async for n in db.nodes.find({"project_id": project_id}, {"_id": 0, "project_id": 0})
    ]
    edges = [
        GraphEdge(**e)
        async for e in db.edges.find({"project_id": project_id}, {"_id": 0, "project_id": 0})
    ]
    return nodes, edges
