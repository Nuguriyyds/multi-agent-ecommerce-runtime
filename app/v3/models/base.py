from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class V3Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
