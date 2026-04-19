from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import Field, field_validator

from app.v3.models import Product
from app.v3.models.base import V3Model
from app.v3.observability import ObservabilityStore

from ..mcp_types import MCPToolDefinition
from .knowledge_base import KnowledgeSnippet, build_knowledge_base, search_product_knowledge

_RAG_PRODUCT_KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer", "minimum": 3, "maximum": 5},
    },
    "required": ["query"],
    "additionalProperties": False,
}

_OBSERVABILITY_METRICS_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
    },
    "required": ["session_id"],
    "additionalProperties": False,
}


class RagProductKnowledgeRequest(V3Model):
    query: str = Field(min_length=1)
    limit: int = Field(default=4, ge=3, le=5)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class ObservabilityMetricsQueryRequest(V3Model):
    session_id: str = Field(min_length=1)

    @field_validator("session_id")
    @classmethod
    def strip_session_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session_id must not be empty")
        return normalized


class MockMCPServer:
    def __init__(
        self,
        *,
        catalog: Sequence[Product] | None = None,
        knowledge_base: Sequence[KnowledgeSnippet] | None = None,
        observability_store: ObservabilityStore | None = None,
    ) -> None:
        self._knowledge_base = [
            snippet.model_copy(deep=True)
            for snippet in (knowledge_base or build_knowledge_base(catalog=catalog))
        ]
        self._observability_store = observability_store
        self._rag_tool_definition = MCPToolDefinition(
            name="rag_product_knowledge",
            description="Search product-buying knowledge snippets derived from the local mock catalog.",
            input_schema=_RAG_PRODUCT_KNOWLEDGE_SCHEMA,
        )
        self._observability_tool_definition = MCPToolDefinition(
            name="observability_metrics_query",
            description="Query session-level runtime metrics and recommendation feedback metrics.",
            input_schema=_OBSERVABILITY_METRICS_QUERY_SCHEMA,
        )

    def list_tool_definitions(self) -> list[MCPToolDefinition]:
        definitions = [self._rag_tool_definition.model_copy(deep=True)]
        if self._observability_store is not None:
            definitions.append(self._observability_tool_definition.model_copy(deep=True))
        return definitions

    async def handle_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "tools.list":
            return {
                "tools": [
                    tool.model_dump(mode="json")
                    for tool in self.list_tool_definitions()
                ]
            }
        if method == "tools.call":
            return await self._handle_tools_call(params)
        raise ValueError(f"Unsupported MCP method: {method}")

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise TypeError("MCP tool arguments must be an object")

        if name == self._rag_tool_definition.name:
            return await self._handle_rag_product_knowledge(arguments)
        if name == self._observability_tool_definition.name and self._observability_store is not None:
            return await self._handle_observability_metrics_query(arguments)
        raise LookupError(f"Unknown MCP tool: {name}")

    async def _handle_rag_product_knowledge(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = RagProductKnowledgeRequest.model_validate(arguments)
        snippets = search_product_knowledge(
            request.query,
            limit=request.limit,
            knowledge_base=self._knowledge_base,
        )
        return {
            "content": [
                {
                    "type": "json",
                    "data": snippet.model_dump(mode="json"),
                }
                for snippet in snippets
            ],
            "is_error": False,
        }

    async def _handle_observability_metrics_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._observability_store is None:
            raise LookupError("observability_metrics_query is not enabled")

        request = ObservabilityMetricsQueryRequest.model_validate(arguments)
        snapshot = self._observability_store.snapshot(request.session_id)
        return {
            "content": [
                {
                    "type": "json",
                    "data": snapshot.model_dump(mode="json"),
                }
            ],
            "is_error": False,
        }


__all__ = [
    "MockMCPServer",
    "ObservabilityMetricsQueryRequest",
    "RagProductKnowledgeRequest",
]
