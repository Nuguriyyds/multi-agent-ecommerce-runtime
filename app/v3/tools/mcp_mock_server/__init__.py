"""In-process mock MCP server and RAG-style product knowledge search."""

from .knowledge_base import KnowledgeSnippet, build_knowledge_base, search_product_knowledge
from .server import MockMCPServer, ObservabilityMetricsQueryRequest, RagProductKnowledgeRequest

__all__ = [
    "KnowledgeSnippet",
    "MockMCPServer",
    "ObservabilityMetricsQueryRequest",
    "RagProductKnowledgeRequest",
    "build_knowledge_base",
    "search_product_knowledge",
]
