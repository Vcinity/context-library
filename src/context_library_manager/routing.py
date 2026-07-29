from .domain import RouteDecision, RouteRequest


def route(request: RouteRequest, excluded_categories: set[str] | None = None) -> RouteDecision:
    checks = ["schema", "identity", "topology", "conflict-key", "parser"]
    if request.category and request.category in (excluded_categories or set()):
        return RouteDecision(
            route="review",
            reason="policy-requires-human-approval",
            deterministic_checks=checks,
            estimated_input_tokens=request.input_tokens,
            profile="review",
        )
    if request.operation in {"source", "publication"} and not request.semantic_fields:
        return RouteDecision(
            route="deterministic",
            reason="pure-maintainer-operation",
            deterministic_checks=checks,
            estimated_input_tokens=request.input_tokens,
        )
    if not request.semantic_fields:
        return RouteDecision(
            route="deterministic",
            reason="all-required-facts-are-exact",
            deterministic_checks=checks,
            estimated_input_tokens=request.input_tokens,
        )
    return RouteDecision(
        route="agent",
        reason="semantic-conflict-assessment-required",
        deterministic_checks=checks,
        estimated_input_tokens=request.input_tokens,
        profile="cheap",
    )
