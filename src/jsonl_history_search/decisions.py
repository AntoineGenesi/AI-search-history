"""Typed optional decisions and deterministic local fallback.

Fixed choices do not confer authority over source safety or chronological anchors.
Rationale: docs/package-map.md#optional-decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol


DecisionType = Literal["choice", "score", "noul"]


@dataclass(frozen=True)
class DecisionQuestion:
    """A fixed typed question plus the local value used on any failure."""

    type: DecisionType
    fallback: object
    criteria: object | None = None
    instructions: object | None = None

    def __post_init__(self) -> None:
        if self.type not in {"choice", "score", "noul"}:
            raise ValueError(f"unsupported decision question type: {self.type}")
        if self.type == "choice":
            if not isinstance(self.criteria, Mapping) or not self.criteria:
                raise ValueError("choice questions require non-empty mapping criteria")
            if self.fallback not in self.criteria:
                raise ValueError("choice fallback must be one of the fixed criteria")
        elif self.type == "score":
            if (
                not isinstance(self.criteria, (list, tuple))
                or not self.criteria
                or not all(isinstance(item, (str, dict, list)) for item in self.criteria)
            ):
                raise ValueError("score questions require non-empty ordered criteria")
            if not isinstance(self.fallback, (int, float)) or isinstance(self.fallback, bool):
                raise ValueError("score fallback must be numeric")
        elif not isinstance(self.fallback, bool):
            raise ValueError("noul fallback must be boolean")

    def api_payload(self) -> dict:
        payload: dict[str, object] = {"type": self.type}
        if self.instructions is not None:
            payload["instructions"] = self.instructions
        if self.criteria is not None:
            payload["criteria"] = self.criteria
        return payload


@dataclass(frozen=True)
class DecisionRequest:
    state: object
    questions: Mapping[str, DecisionQuestion]
    contains_source_content: bool = False

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError("a decision request requires at least one named question")
        if not all(isinstance(name, str) and name.strip() for name in self.questions):
            raise ValueError("decision question names must be non-empty strings")

    def api_payload(self, *, model: str) -> dict:
        return {
            "state": self.state,
            "model": model,
            "questions": {
                name: question.api_payload()
                for name, question in self.questions.items()
            },
        }


@dataclass(frozen=True)
class DecisionAnswer:
    type: DecisionType
    value: object
    confidence: float | None
    probabilities: Mapping[str, float]
    used_fallback: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "value": self.value,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
            "used_fallback": self.used_fallback,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DecisionResponse:
    engine: str
    model: str | None
    answers: Mapping[str, DecisionAnswer]
    fallback_reason: str | None = None
    usage: Mapping[str, int] | None = None
    receipt: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "engine": self.engine,
            "model": self.model,
            "fallback_reason": self.fallback_reason,
            "usage": dict(self.usage) if self.usage is not None else None,
            "receipt": dict(self.receipt),
            "answers": {name: answer.as_dict() for name, answer in self.answers.items()},
        }


class DecisionEngine(Protocol):
    name: str

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        ...


class DeterministicDecisionEngine:
    """The local default: always use the caller's explicit fallback values."""

    name = "deterministic"

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        return fallback_response(
            request,
            engine=self.name,
            reason="deterministic_default",
        )


def fallback_response(
    request: DecisionRequest,
    *,
    engine: str,
    reason: str,
    model: str | None = None,
) -> DecisionResponse:
    return DecisionResponse(
        engine=engine,
        model=model,
        fallback_reason=reason,
        answers={
            name: DecisionAnswer(
                type=question.type,
                value=question.fallback,
                confidence=None,
                probabilities={},
                used_fallback=True,
                reason=reason,
            )
            for name, question in request.questions.items()
        },
    )
