"""Deterministic eligibility and ranking with exact-decimal budget bounds."""

from decimal import Decimal

from app.errors import DomainError
from app.routing_schema import CandidateEvaluation, RankedCandidates


def rejection_reasons(
    candidate,
    *,
    capability,
    policy,
    project_id,
    budget,
    allow_remote,
    requirements,
    estimate,
    check_remaining_budget=True,
):
    reasons = []
    if not candidate.enabled:
        reasons.append("CANDIDATE_DISABLED")
    if candidate.health == "unhealthy":
        reasons.append("CANDIDATE_UNHEALTHY")
    if capability not in candidate.capabilities:
        reasons.append("CAPABILITY_UNSUPPORTED")
    if requirements.remote_only and candidate.is_mock:
        reasons.append("CAPABILITY_UNSUPPORTED")
    if policy.allowed_ids is not None and candidate.id not in policy.allowed_ids:
        reasons.append("CANDIDATE_OUTSIDE_SCOPE")
    if not candidate.all_projects and str(project_id) not in map(str, candidate.project_ids):
        reasons.append("CANDIDATE_NOT_AUTHORIZED")
    if requirements.timestamps and not candidate.timestamps:
        reasons.append("TIMESTAMPS_UNSUPPORTED")
    if set(requirements.reference_roles) - set(candidate.reference_roles):
        reasons.append("REFERENCE_ROLE_UNSUPPORTED")
    if candidate.quality is None:
        if policy.unknown_quality == "exclude":
            reasons.append("QUALITY_UNKNOWN")
    elif policy.minimum_quality is not None and candidate.quality.grade < policy.minimum_quality:
        reasons.append("QUALITY_BELOW_MINIMUM")
    if candidate.latency is None:
        if policy.unknown_latency == "exclude":
            reasons.append("LATENCY_UNKNOWN")
    elif policy.max_latency_ms is not None and candidate.latency.p95_ms > policy.max_latency_ms:
        reasons.append("LATENCY_ABOVE_MAXIMUM")
    if not candidate.is_mock:
        if not allow_remote or not budget.get("allow_remote", True):
            reasons.append("PROJECT_REMOTE_BLOCKED")
        if candidate.currency != budget["currency"]:
            reasons.append("BUDGET_CURRENCY_MISMATCH")
        if estimate is None:
            if policy.unknown_cost == "exclude" or not budget.get("allow_unknown_cost", True):
                reasons.append("UNKNOWN_COST_BLOCKED")
        else:
            used = Decimal(str(budget["spent"])) + Decimal(str(budget["reserved"]))
            if check_remaining_budget and estimate + used > Decimal(str(budget["budget_limit"])):
                reasons.append("BUDGET_EXCEEDED")
    if policy.max_estimated_cost is not None:
        if estimate is None or estimate > policy.max_estimated_cost:
            reasons.append("ROUTE_COST_BOUND_EXCEEDED")
    return reasons


def ranking_key(candidate, policy, estimate):
    cost = (estimate is None, estimate or Decimal(0))
    quality = (candidate.quality is None, -candidate.quality.grade if candidate.quality else 0)
    latency = (candidate.latency is None, candidate.latency.p95_ms if candidate.latency else 0)
    if policy.strategy == "preferred":
        preference = {identity: index for index, identity in enumerate(policy.preferred_ids)}
        return (
            preference.get(candidate.id, len(preference)),
            candidate.priority,
            cost,
            candidate.id,
        )
    if policy.strategy == "lowest_estimated_cost":
        return (cost, candidate.priority, quality, latency, candidate.id)
    if policy.strategy == "quality_first":
        return (quality, candidate.priority, cost, latency, candidate.id)
    return (candidate.priority, cost, quality, latency, candidate.id)


def rank_candidates(
    candidates,
    *,
    capability,
    policy,
    project_id,
    budget,
    allow_remote,
    requirements,
    check_remaining_budget=True,
):
    if len({candidate.id for candidate in candidates}) != len(candidates):
        raise DomainError("DUPLICATE_CANDIDATE", "Routing candidate identities must be unique", 422)
    evaluations = []
    eligible = []
    for candidate in sorted(candidates, key=lambda value: value.id):
        estimate = None
        if candidate.estimated_cost is not None:
            estimate = candidate.estimated_cost * requirements.units
        rejections = rejection_reasons(
            candidate,
            capability=capability,
            policy=policy,
            project_id=project_id,
            budget=budget,
            allow_remote=allow_remote,
            requirements=requirements,
            estimate=estimate,
            check_remaining_budget=check_remaining_budget,
        )
        evaluations.append(
            CandidateEvaluation(
                candidate_id=candidate.id,
                config_revision=candidate.config_revision,
                model=candidate.model,
                estimated_cost=estimate,
                currency=candidate.currency,
                quality=candidate.quality,
                latency=candidate.latency,
                rejections=rejections,
            )
        )
        if not rejections:
            eligible.append((ranking_key(candidate, policy, estimate), candidate.id))
    if not eligible:
        raise DomainError(
            "NO_ELIGIBLE_CANDIDATE",
            "No model connection meets the routing policy",
            409,
            {"candidates": [item.model_dump(mode="json") for item in evaluations]},
        )
    identities = [identity for _, identity in sorted(eligible)]
    return RankedCandidates(
        selected_id=identities[0], eligible_ids=identities, evaluations=evaluations
    )
