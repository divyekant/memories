"""Data models for the Memories efficacy eval harness."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RubricType(str, Enum):
    contains = "contains"
    not_contains = "not_contains"
    correct_fix = "correct_fix"
    no_retry = "no_retry"
    match_convention = "match_convention"
    recall_accuracy = "recall_accuracy"


class Rubric(BaseModel):
    type: RubricType
    value: Optional[str] = None
    description: Optional[str] = None
    weight: float = Field(ge=0.0, le=1.0)


class MemorySeed(BaseModel):
    text: str
    source: str


class Scenario(BaseModel):
    id: str
    category: str
    name: str
    description: str
    memories: list[MemorySeed]
    prompt: str
    expected: list[Rubric]


class EvalConfig(BaseModel):
    memories_url: str = "http://localhost:8900"
    memories_api_key: str = ""
    judge_provider: str = "anthropic"
    cc_timeout: int = 120
    category_weights: dict[str, float] = Field(
        default_factory=lambda: {"coding": 0.40, "recall": 0.35, "compounding": 0.25}
    )


class RubricResult(BaseModel):
    rubric_type: str
    score: float
    weight: float
    reasoning: str = ""


class TestResult(BaseModel):
    scenario_id: str
    scenario_name: str
    category: str
    score_with_memory: float
    score_without_memory: float
    output_with_memory: str
    output_without_memory: str
    rubric_details: list[RubricResult]

    @property
    def efficacy_delta(self) -> float:
        return self.score_with_memory - self.score_without_memory


class CategoryResult(BaseModel):
    category: str
    with_memory: float
    without_memory: float
    delta: float


class EvalReport(BaseModel):
    version: str = "1.0.0"
    timestamp: str = ""
    overall_with_memory: float = 0.0
    overall_without_memory: float = 0.0
    overall_efficacy_delta: float = 0.0
    categories: dict[str, CategoryResult] = Field(default_factory=dict)
    tests: list[TestResult] = Field(default_factory=list)
