import os
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def connect() -> AsyncIOMotorDatabase:
    global _client, _db
    url     = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("MONGODB_DB",  "meshai")
    _client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    _db     = _client[db_name]
    await _db.command("ping")          # raises if unreachable
    await _db.projects.create_index([("id", 1)], unique=True)
    await _db.nodes.create_index([("project_id", 1)])
    await _db.nodes.create_index([("id", 1)], unique=True)
    await _db.edges.create_index([("project_id", 1)])
    print(f"[MongoDB] Connected → {db_name}")
    return _db


def get_db() -> AsyncIOMotorDatabase:
    return _db


async def disconnect():
    global _client
    if _client:
        _client.close()
