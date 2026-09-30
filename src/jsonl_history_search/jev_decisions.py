from __future__ import annotations

import json
from typing import Callable, Mapping

from .decisions import (
    DecisionAnswer,
    DecisionRequest,
    DecisionResponse,
    fallback_response,
)


DEFAULT_SYSTEMONE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JsonTransport = Callable[[str, dict, Mapping[str, str], float], dict]


class SystemOneDecisionEngine:
    """Lazy, fail-closed adapter for TypeSafe's typed System One endpoint."""

    name = "jev"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None,
        endpoint: str = DEFAULT_SYSTEMONE_ENDPOINT,
        timeout: float = 2.0,
        min_confidence: float = 0.75,
        allow_remote_content: bool = False,
        transport: JsonTransport | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Jev timeout must be greater than zero")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("decision confidence must be between zero and one")
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.endpoint = endpoint.strip() or DEFAULT_SYSTEMONE_ENDPOINT
        self.timeout = timeout
        self.min_confidence = min_confidence
        self.allow_remote_content = allow_remote_content
        self._transport = transport

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        if not self.api_key:
            return fallback_response(
                request,
                engine=self.name,
                model=self.model or None,
                reason="missing_api_key",
            )
        if not self.model:
            return fallback_response(
                request,
                engine=self.name,
                reason="missing_model",
            )
        if request.contains_source_content and not self.allow_remote_content:
            return fallback_response(
                request,
                engine=self.name,
                model=self.model,
                reason="remote_source_content_not_allowed",
            )

        payload = request.api_payload(model=self.model)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            transport = self._transport or _systemone_http_transport
            raw = transport(self.endpoint, payload, headers, self.timeout)
            return self._parse_response(request, raw)
        except Exception as exc:  # Network/schema failures must preserve the local route.
            return fallback_response(
                request,
                engine=self.name,
                model=self.model,
                reason=f"jev_unavailable:{type(exc).__name__}",
            )

    def _parse_response(self, request: DecisionRequest, raw: object) -> DecisionResponse:
        if not isinstance(raw, dict) or not isinstance(raw.get("answers"), dict):
            raise ValueError("invalid System One response")
        raw_answers = raw["answers"]
        answers: dict[str, DecisionAnswer] = {}
        for name, question in request.questions.items():
            answer = raw_answers.get(name)
            try:
                answers[name] = self._parse_answer(question, answer)
            except (KeyError, TypeError, ValueError):
                answers[name] = DecisionAnswer(
                    type=question.type,
                    value=question.fallback,
                    confidence=None,
                    probabilities={},
                    used_fallback=True,
                    reason="invalid_typed_answer",
                )
        fallback_reasons = sorted(
            {answer.reason for answer in answers.values() if answer.used_fallback}
        )
        return DecisionResponse(
            engine=self.name,
            model=str(raw.get("model") or self.model),
            answers=answers,
            fallback_reason=",".join(fallback_reasons) or None,
        )

    def _parse_answer(self, question, answer: object) -> DecisionAnswer:
        if not isinstance(answer, dict) or answer.get("type") != question.type:
            raise ValueError("answer type does not match question")

        if question.type == "choice":
            value = answer["choice"]
            if not isinstance(value, str) or value not in question.criteria:
                raise ValueError("choice is outside fixed criteria")
            confidence = _probability(answer["confidence"])
            probabilities = _probability_map(answer["probabilities"])
        elif question.type == "score":
            value = answer["score"]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("score is not numeric")
            value = float(value)
            if value < 0 or value > len(question.criteria) - 1:
                raise ValueError("score is outside the requested rubric")
            if not isinstance(answer["legend"], dict):
                raise ValueError("score legend is not an object")
            confidence = _probability(answer["confidence"])
            probabilities = _probability_map(answer["probabilities"])
        else:
            probability = _probability(answer["noul"])
            value = probability >= 0.5
            confidence = max(probability, 1.0 - probability)
            probabilities = {"true": probability, "false": 1.0 - probability}

        if confidence < self.min_confidence:
            return DecisionAnswer(
                type=question.type,
                value=question.fallback,
                confidence=confidence,
                probabilities=probabilities,
                used_fallback=True,
                reason="below_confidence_threshold",
            )
        return DecisionAnswer(
            type=question.type,
            value=value,
            confidence=confidence,
            probabilities=probabilities,
            used_fallback=False,
            reason="accepted",
        )


def _probability(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("probability must be numeric")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError("probability must be between zero and one")
    return result


def _probability_map(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("probabilities must be an object")
    return {str(key): _probability(probability) for key, probability in value.items()}


def _systemone_http_transport(
    endpoint: str,
    payload: dict,
    headers: Mapping[str, str],
    timeout: float,
) -> dict:
    # Keep the optional remote path dependency-free and lazy at call time.
    from urllib.request import Request, urlopen

    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("System One response must be a JSON object")
    return decoded
