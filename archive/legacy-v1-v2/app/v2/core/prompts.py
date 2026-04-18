from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class PromptTemplate(BaseModel):
    name: str
    version: str
    template: str
    variables_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "required": [],
        },
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) < 2 or not all(parts):
            raise ValueError("prompt name must use dotted.path format")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("prompt version must be non-empty")
        return cleaned


class PromptRegistry:
    def __init__(self) -> None:
        self._prompts: dict[str, dict[str, PromptTemplate]] = {}
        self._version_order: dict[str, list[str]] = {}

    def register(self, prompt: PromptTemplate) -> PromptTemplate:
        versions = self._prompts.setdefault(prompt.name, {})
        if prompt.version in versions:
            raise ValueError(
                f"prompt '{prompt.name}' version '{prompt.version}' is already registered",
            )
        versions[prompt.version] = prompt
        self._version_order.setdefault(prompt.name, []).append(prompt.version)
        return prompt

    def has(self, name: str, version: str | None = None) -> bool:
        if name not in self._prompts:
            return False
        if version is None:
            return True
        return version in self._prompts[name]

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        if name not in self._prompts:
            raise KeyError(f"unknown prompt: {name}")
        if version is None:
            version = self._version_order[name][-1]
        try:
            return self._prompts[name][version]
        except KeyError as exc:
            raise KeyError(f"unknown prompt version: {name}@{version}") from exc

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._prompts))

    def list_versions(self, name: str) -> tuple[str, ...]:
        if name not in self._version_order:
            raise KeyError(f"unknown prompt: {name}")
        return tuple(self._version_order[name])

    def render(
        self,
        name: str,
        *,
        variables: dict[str, Any] | None = None,
        version: str | None = None,
    ) -> str:
        prompt = self.get(name, version=version)
        payload = dict(variables or {})
        required = tuple(prompt.variables_schema.get("required", ()))
        missing = tuple(
            field
            for field in required
            if field not in payload or payload[field] in (None, "", [], {}, ())
        )
        if missing:
            raise ValueError(
                f"missing required prompt variables: {', '.join(missing)}",
            )

        try:
            return prompt.template.format(**payload)
        except KeyError as exc:
            raise ValueError(
                f"missing required prompt variables: {exc.args[0]}",
            ) from exc


def build_default_prompt_registry() -> PromptRegistry:
    registry = PromptRegistry()
    default_prompts = (
        PromptTemplate(
            name="shopping.manager.plan",
            version="v1",
            template=(
                "用户消息: {message}\n"
                "场景: {scene}\n"
                "用户画像摘要: {profile_summary}\n"
                "请生成本轮 ShoppingManager 的执行计划。"
            ),
            variables_schema={
                "type": "object",
                "required": ["message", "scene", "profile_summary"],
            },
        ),
        PromptTemplate(
            name="shopping.manager.clarify",
            version="v1",
            template=(
                "当前场景: {scene}\n"
                "仍缺少的信息: {missing_fields}\n"
                "请向用户发起一次简洁追问。"
            ),
            variables_schema={
                "type": "object",
                "required": ["scene", "missing_fields"],
            },
        ),
        PromptTemplate(
            name="shopping.manager.respond",
            version="v1",
            template=(
                "基于以下结果生成最终回复。\n"
                "回复提纲: {reply_outline}\n"
                "候选商品: {products}"
            ),
            variables_schema={
                "type": "object",
                "required": ["reply_outline", "products"],
            },
        ),
        PromptTemplate(
            name="preference.extract",
            version="v1",
            template=(
                "从用户表达中抽取预算、品类、品牌、用途、排除项。\n"
                "消息: {message}\n"
                "当前 session memory: {session_memory}"
            ),
            variables_schema={
                "type": "object",
                "required": ["message", "session_memory"],
            },
        ),
        PromptTemplate(
            name="comparison.summarize",
            version="v1",
            template=(
                "比较以下商品，并围绕 {focus} 输出结构化差异总结:\n"
                "{products}"
            ),
            variables_schema={
                "type": "object",
                "required": ["products", "focus"],
            },
        ),
        PromptTemplate(
            name="copy.generate",
            version="v1",
            template=(
                "面向 {audience}，为商品 {product_name} 生成一句营销文案。\n"
                "卖点: {selling_points}"
            ),
            variables_schema={
                "type": "object",
                "required": ["audience", "product_name", "selling_points"],
            },
        ),
    )

    for prompt in default_prompts:
        registry.register(prompt)

    return registry


__all__ = [
    "PromptRegistry",
    "PromptTemplate",
    "build_default_prompt_registry",
]
