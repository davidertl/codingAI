import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orchestrator.utils import sha256_text

_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,600}$")


def _normalize_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value:
        raise ValueError("file path cannot be empty")
    if value.startswith("/") or value.startswith("../") or "/../" in f"/{value}":
        raise ValueError(f"unsafe file path: {path}")
    if not _SAFE_PATH_RE.match(value):
        raise ValueError(f"invalid file path: {path}")
    return value


class TaskClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal["debug", "feature", "refactor", "analyze", "test_writing", "security"] = "feature"
    complexity: Literal["low", "medium", "high"] = "medium"
    required_skills: list[str] = Field(default_factory=list, max_length=200)
    suggested_tools: list[str] = Field(default_factory=list, max_length=200)
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    summary_prompt: str = Field(default="", max_length=5000)

    @field_validator("required_skills", "suggested_tools")
    @classmethod
    def _strip_list_cls(cls, values: list[str]) -> list[str]:
        return [s.strip() for s in (values or []) if str(s or "").strip()]


class TaskSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(..., min_length=3, max_length=5000)
    requirements: list[str] = Field(default_factory=list, max_length=500)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=500)
    affected_modules: list[str] = Field(default_factory=list, max_length=200)
    constraints: list[str] = Field(default_factory=list, max_length=200)
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    ambiguity_notes: list[str] = Field(default_factory=list, max_length=200)
    source_issue_number: int | None = None
    source_repo: str = ""

    @field_validator("requirements", "acceptance_criteria", "affected_modules", "constraints", "ambiguity_notes")
    @classmethod
    def _strip_list(cls, values: list[str]) -> list[str]:
        out = []
        for item in values or []:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out


class ExecutionPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=2, max_length=300)
    details: str = Field(..., min_length=2, max_length=3000)
    owner_role: Literal["planner", "researcher", "coder", "reviewer", "test_interpreter", "judge"] = "planner"
    expected_output: str = Field(..., min_length=2, max_length=1000)


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=3, max_length=5000)
    steps: list[ExecutionPlanStep] = Field(default_factory=list, max_length=200)
    likely_affected_files: list[str] = Field(default_factory=list, max_length=800)
    test_strategy: list[str] = Field(default_factory=list, max_length=100)
    risk_analysis: str = Field(default="", max_length=4000)
    complexity_score: int = Field(ge=0, le=10)
    routing_guidance: str = Field(default="", max_length=1000)
    hypothesis: str = Field(default="", max_length=2000)
    root_cause: str = Field(default="", max_length=2000)
    rollback_strategy: str = Field(default="", max_length=2000)

    @field_validator("likely_affected_files")
    @classmethod
    def _normalize_files(cls, files: list[str]) -> list[str]:
        seen = set()
        out = []
        for path in files or []:
            normalized = _normalize_path(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=500)
    url: str = Field(..., min_length=6, max_length=2000)
    snippet: str = Field(default="", max_length=2000)


class ResearchBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[ResearchSource] = Field(default_factory=list, max_length=80)
    technical_summary: str = Field(default="", max_length=12000)
    assumptions: list[str] = Field(default_factory=list, max_length=200)
    uncertainty_notes: list[str] = Field(default_factory=list, max_length=200)


class FileArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=600)
    content: str = Field(..., min_length=0, max_length=2_000_000)
    sha256: str = Field(default="", min_length=0, max_length=64)

    @field_validator("path")
    @classmethod
    def _normalize_file_path(cls, value: str) -> str:
        return _normalize_path(value)

    @model_validator(mode="after")
    def _fill_sha(self):
        if not self.sha256:
            self.sha256 = sha256_text(self.content)
        return self


class CodeBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[FileArtifact] = Field(default_factory=list, max_length=1000)
    delete_paths: list[str] = Field(default_factory=list, max_length=1000)
    rationale: str = Field(default="", max_length=5000)
    assumptions: list[str] = Field(default_factory=list, max_length=300)
    testing_notes: list[str] = Field(default_factory=list, max_length=300)

    @field_validator("delete_paths")
    @classmethod
    def _normalize_delete_paths(cls, files: list[str]) -> list[str]:
        out = []
        seen = set()
        for path in files or []:
            normalized = _normalize_path(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    @model_validator(mode="after")
    def _check_unique_paths(self):
        seen = set()
        for artifact in self.files:
            if artifact.path in seen:
                raise ValueError(f"duplicate file artifact path: {artifact.path}")
            seen.add(artifact.path)
        for path in self.delete_paths:
            normalized = _normalize_path(path)
            if normalized in seen:
                raise ValueError(f"delete path duplicates created/updated artifact: {normalized}")
            seen.add(normalized)
        return self


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["info", "warning", "error"] = "info"
    message: str = Field(..., min_length=2, max_length=3000)
    evidence_file: str = Field(default="", max_length=600)
    evidence_reasoning: str = Field(default="", max_length=4000)

    @field_validator("evidence_file")
    @classmethod
    def _normalize_evidence_file(cls, value: str) -> str:
        v = str(value or "").strip()
        if not v:
            return ""
        return _normalize_path(v)


class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=6000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=2000)
    assertions: dict[str, bool] = Field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(f.level == "error" for f in self.findings)

    @property
    def has_significant_errors(self) -> bool:
        return self.has_errors


class TestFailureAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symptom: str = Field(..., min_length=2, max_length=6000)
    root_cause: str = Field(default="", max_length=6000)
    root_cause_type: Literal["code", "environment", "unknown"] = "unknown"
    evidence: list[str] = Field(default_factory=list, max_length=300)
    recommended_actions: list[str] = Field(default_factory=list, max_length=300)


class JudgeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["A", "B", "MERGE"] = "A"
    reason: str = Field(..., min_length=3, max_length=6000)
    requires_retest: bool = True
    merged_paths: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("merged_paths")
    @classmethod
    def _normalize_merged_paths(cls, files: list[str]) -> list[str]:
        out = []
        seen = set()
        for path in files or []:
            normalized = _normalize_path(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out


class RunOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., min_length=8, max_length=120)
    repo: str = Field(..., min_length=1, max_length=200)
    issue_number: int | None = None
    status: Literal["success", "failed", "aborted"] = "failed"
    summary: str = Field(default="", max_length=12000)
    success_criteria: dict[str, bool] = Field(default_factory=dict)
    error_reason: str = Field(default="", max_length=6000)
    artifacts: dict[str, str] = Field(default_factory=dict)
    attempts_count: int = Field(default=0, ge=0, le=999)
    ended_at_utc: str = Field(default="")
