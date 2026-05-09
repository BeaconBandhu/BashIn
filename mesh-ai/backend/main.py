import os
import sys
import uuid
import json
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from models.schemas import (
    AISource, CreateProjectRequest, GraphData, GraphEdge, GraphNode,
    IngestRequest, NodeType, Project, QueryRequest,
)
from connectors.md_importer import MarkdownImporter
from connectors.openai_connector import OpenAIConnector
from connectors.anthropic_connector import AnthropicConnector
from connectors.gemini_connector import GeminiConnector
from extraction.embedder import Embedder
from extraction.ngram_extractor import NGramExtractor
from extraction.triple_extractor import TripleExtractor
from classifier.node_classifier import NodeClassifier
from graph.store import GraphStore
from graph.algorithms import GraphAlgorithms
from graph.query_router import QueryRouter
from db.mongo import connect, disconnect, get_db
from db.project_store import ProjectStore, save_graph, load_graph

# ── singleton ML components ───────────────────────────────────────────────────
embedder    = Embedder()
ngram       = NGramExtractor()
triples     = TripleExtractor(openai_api_key=os.getenv("OPENAI_API_KEY"))
classifier  = NodeClassifier(embedder)
md_importer = MarkdownImporter()

openai_conn    = OpenAIConnector(api_key=os.getenv("OPENAI_API_KEY"))
anthropic_conn = AnthropicConnector(api_key=os.getenv("ANTHROPIC_API_KEY"))
gemini_conn    = GeminiConnector(api_key=os.getenv("GEMINI_API_KEY"))

# ── per-project in-memory graphs ──────────────────────────────────────────────
_stores: Dict[str, GraphStore]      = {}
_algos:  Dict[str, GraphAlgorithms] = {}
_routers: Dict[str, QueryRouter]    = {}
_project_store: Optional[ProjectStore] = None
mongo_ok = False


def _get_store(project_id: str) -> Tuple[GraphStore, GraphAlgorithms, QueryRouter]:
    if project_id not in _stores:
        s = GraphStore()
        _stores[project_id]  = s
        _algos[project_id]   = GraphAlgorithms(s)
        _routers[project_id] = QueryRouter(s, embedder)
    return _stores[project_id], _algos[project_id], _routers[project_id]


async def _persist(project_id: str):
    if mongo_ok:
        s, _, _ = _get_store(project_id)
        db = get_db()
        await save_graph(db, project_id, s.get_all_nodes(), s.get_all_edges())
        await _project_store.update_node_count(project_id, len(s.get_all_nodes()))


# ── startup / shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mongo_ok, _project_store
    try:
        db = await connect()
        _project_store = ProjectStore(db)
        mongo_ok = True
        projects = await _project_store.get_all()
        for p in projects:
            nodes, edges = await load_graph(db, p.id)
            s, _, _ = _get_store(p.id)
            for node in nodes:
                s.add_node(node)
            for edge in edges:
                s.add_edge(edge)
            _algos[p.id].compute_all()
        print(f"[MeshAI] Loaded {len(projects)} project(s) from MongoDB")
    except Exception as e:
        print(f"[MeshAI] MongoDB unavailable ({e}). Running without persistence.")
        mongo_ok = False
    yield
    if mongo_ok:
        await disconnect()


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="MeshAI", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _process_chunk(text: str, source: AISource, project_id: str) -> List[str]:
    s, _, _ = _get_store(project_id)
    emb      = embedder.embed(text)
    existing = [n.model_dump() for n in s.get_all_nodes()]

    ntype = classifier.classify(text, emb, source, existing)
    label = (text[:70] + "…") if len(text) > 70 else text
    node  = GraphNode(id=str(uuid.uuid4()), label=label, type=ntype,
                      source=source, text=text, embedding=emb)
    chunk_id = s.add_node(node)
    added    = [chunk_id]

    concepts     = ngram.extract_concepts(text, top_n=20)
    cooccurrences = ngram.extract_cooccurrences(text)
    concept_ids: dict = {}

    for concept in concepts:
        cemb   = embedder.embed(concept)
        sim    = s.find_similar_nodes(cemb, top_k=1)
        if sim and sim[0][0] > 0.92:
            cid = sim[0][1].id
        else:
            ex2   = [n.model_dump() for n in s.get_all_nodes()]
            ctype = classifier.classify(concept, cemb, source, ex2)
            cn    = GraphNode(id=str(uuid.uuid4()), label=concept, type=ctype,
                              source=source, text=concept, embedding=cemb)
            cid   = s.add_node(cn)
            added.append(cid)
        concept_ids[concept] = cid
        s.add_edge(GraphEdge(source=chunk_id, target=cid, weight=1.0, relation="contains"))

    for a, b, w in cooccurrences:
        if a in concept_ids and b in concept_ids:
            s.add_edge(GraphEdge(source=concept_ids[a], target=concept_ids[b],
                                 weight=w, relation="co-occurs"))

    for subj, pred, obj in triples.extract(text):
        s_sim = s.find_similar_nodes(embedder.embed(subj), top_k=1)
        o_sim = s.find_similar_nodes(embedder.embed(obj),  top_k=1)
        sid   = s_sim[0][1].id if s_sim and s_sim[0][0] > 0.88 else None
        oid   = o_sim[0][1].id if o_sim and o_sim[0][0] > 0.88 else None
        if sid and oid:
            s.add_edge(GraphEdge(source=sid, target=oid, weight=1.5, relation=pred))

    return added


def _export_md(project_name: str, nodes: List[GraphNode], edges: List[GraphEdge]) -> str:
    by_type: Dict[str, List[GraphNode]] = defaultdict(list)
    for n in nodes:
        by_type[n.type].append(n)
    for t in by_type:
        by_type[t].sort(key=lambda n: -(n.importance or 0))

    node_map = {n.id: n for n in nodes}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sources = sorted({n.source for n in nodes})

    lines = [
        f"# Knowledge Export — {project_name}",
        f"",
        f"> Generated by MeshAI on {now}  ",
        f"> Nodes: {len(nodes)} | Edges: {len(edges)} | Sources: {', '.join(sources)}",
        f"",
        "Paste this file as context in any AI chatbot to continue your work.",
        "Garbage nodes show what was previously wrong — avoid those paths.",
        "",
        "---",
        "",
    ]

    sections = [
        ("memory",  "## Verified Knowledge",     "Facts confirmed across sessions."),
        ("branch",  "## Cross-AI Insights",       "Synthesised from multiple AI sources."),
        ("tree",    "## New Concepts",             "Unique ideas that emerged during research."),
        ("string",  "## Shared Concepts",          "Same idea appeared across different AIs."),
        ("garbage", "## Known Incorrect Info",     "These answers were wrong — do not trust them."),
    ]

    for key, heading, desc in sections:
        items = by_type.get(key, [])
        if not items:
            continue
        lines += [heading, f"*{desc}*", ""]
        for n in items[:25]:
            lines.append(f"**[{n.source.upper()}]** {n.label}")
            if n.text and n.text != n.label and len(n.text) > len(n.label):
                preview = n.text[:300].replace("\n", " ")
                if len(n.text) > 300:
                    preview += "…"
                lines.append(f"> {preview}")
            lines.append("")

    top_edges = sorted(edges, key=lambda e: -(e.weight or 0))[:15]
    if top_edges:
        lines += ["## Key Relationships", ""]
        for e in top_edges:
            src = node_map.get(e.source)
            tgt = node_map.get(e.target)
            if src and tgt:
                rel = e.relation or "relates-to"
                lines.append(f"- `{src.label}` **—{rel}→** `{tgt.label}`")
        lines.append("")

    return "\n".join(lines)


# ── project endpoints ─────────────────────────────────────────────────────────

@app.post("/projects", response_model=Project)
async def create_project(body: CreateProjectRequest):
    if not mongo_ok:
        p = Project(id=str(uuid.uuid4()), name=body.name,
                    description=body.description,
                    created_at=datetime.now().isoformat())
        _get_store(p.id)
        return p
    return await _project_store.create(body.name, body.description)


@app.get("/projects", response_model=List[Project])
async def list_projects():
    if not mongo_ok:
        return [
            Project(id=pid, name=pid[:8], created_at="",
                    node_count=len(_stores[pid].get_all_nodes()))
            for pid in _stores
        ]
    projects = await _project_store.get_all()
    # sync node counts from live memory
    for p in projects:
        if p.id in _stores:
            p.node_count = len(_stores[p.id].get_all_nodes())
    return projects


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    if mongo_ok:
        await _project_store.delete(project_id)
    if project_id in _stores:
        del _stores[project_id]
        del _algos[project_id]
        del _routers[project_id]
    return {"status": "deleted"}


# ── ingest endpoints ──────────────────────────────────────────────────────────

@app.post("/projects/{project_id}/ingest")
async def ingest_text(project_id: str, request: IngestRequest):
    _get_store(project_id)
    chunks = md_importer.parse(request.content, request.session_id)
    if not chunks:
        raise HTTPException(400, "No parseable content found")

    added = []
    for chunk in chunks:
        added.extend(_process_chunk(chunk.text, request.source, project_id))

    _algos[project_id].compute_all()
    await _persist(project_id)
    return {"status": "ok", "chunks": len(chunks), "nodes_added": len(added)}


@app.post("/projects/{project_id}/ingest/files")
async def ingest_files(
    project_id: str,
    files:   List[UploadFile] = File(...),
    sources: str              = Form("[]"),   # JSON array matching files order
):
    _get_store(project_id)
    try:
        src_list: List[str] = json.loads(sources)
    except Exception:
        src_list = []

    results = []
    for i, file in enumerate(files):
        raw    = (await file.read()).decode("utf-8", errors="replace")
        src    = src_list[i] if i < len(src_list) else "markdown"
        ai_src = AISource(src) if src in AISource._value2member_map_ else AISource.MARKDOWN
        chunks = md_importer.parse(raw, str(uuid.uuid4()))
        added  = []
        for chunk in chunks:
            added.extend(_process_chunk(chunk.text, ai_src, project_id))
        results.append({"file": file.filename, "chunks": len(chunks), "nodes": len(added)})

    _algos[project_id].compute_all()
    await _persist(project_id)
    return {"files": len(files), "results": results}


# ── graph endpoints ───────────────────────────────────────────────────────────

@app.get("/projects/{project_id}/graph", response_model=GraphData)
async def get_graph(project_id: str):
    s, a, _ = _get_store(project_id)
    a.compute_all()
    return s.get_graph_data()


@app.post("/projects/{project_id}/query")
async def query_graph(project_id: str, request: QueryRequest):
    _, _, r = _get_store(project_id)
    return r.get_subgraph(request.query, request.top_k)


@app.get("/projects/{project_id}/stats")
async def project_stats(project_id: str):
    s, _, _ = _get_store(project_id)
    nodes = s.get_all_nodes()
    return {
        "total_nodes": len(nodes),
        "total_edges": len(s.get_all_edges()),
        "by_type":     dict(Counter(n.type   for n in nodes)),
        "by_source":   dict(Counter(n.source for n in nodes)),
    }


# ── export ────────────────────────────────────────────────────────────────────

@app.get("/projects/{project_id}/export")
async def export_project(project_id: str):
    s, a, _ = _get_store(project_id)
    a.compute_all()
    nodes = s.get_all_nodes()
    edges = s.get_all_edges()

    project_name = project_id
    if mongo_ok:
        p = await _project_store.get(project_id)
        if p:
            project_name = p.name

    md = _export_md(project_name, nodes, edges)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)
    return Response(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_knowledge.md"'},
    )


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "mongo": mongo_ok}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
