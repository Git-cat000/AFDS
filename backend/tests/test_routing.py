"""Model-route eligibility and deterministic policy ranking, without provider calls."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.errors import DomainError
from app.routing_schema import ProviderCandidate, RoutingPolicy, RoutingRequirements
from app.routing_selection import rank_candidates

PROJECT_ID = str(uuid4())


def candidate(identity, **values):
    return ProviderCandidate.model_validate(
        {
            "id": identity,
            "label": "DEMO candidate",
            "kind": "llm",
            "config_revision": 1,
            "model": "DEMO model",
            "capabilities": ["storyboard"],
            "enabled": True,
            "estimated_cost": "0.25",
            "currency": "USD",
            **values,
        }
    )


def budget(**values):
    return {
        "budget_limit": "10",
        "spent": "0",
        "reserved": "0",
        "currency": "USD",
        "allow_unknown_cost": True,
        "allow_remote": True,
        **values,
    }


def rank(
    candidates,
    *,
    policy=None,
    finance=None,
    requirements=None,
    capability="storyboard",
    allow_remote=True,
):
    return rank_candidates(
        candidates,
        capability=capability,
        policy=policy or RoutingPolicy(),
        project_id=PROJECT_ID,
        budget=finance or budget(),
        allow_remote=allow_remote,
        requirements=requirements or RoutingRequirements(),
    )


def test_routing_filters_capability_currency_and_project_authorization():
    values = [
        candidate("demo-video", capabilities=["text_to_video"], kind="video"),
        candidate("demo-eur", currency="EUR"),
        candidate("demo-private", all_projects=False, project_ids=[str(uuid4())]),
        candidate("demo-valid"),
    ]
    result = rank(values)
    assert result.selected_id == "demo-valid"
    reasons = {item.candidate_id: item.rejections for item in result.evaluations}
    assert "CAPABILITY_UNSUPPORTED" in reasons["demo-video"]
    assert "BUDGET_CURRENCY_MISMATCH" in reasons["demo-eur"]
    assert "CANDIDATE_NOT_AUTHORIZED" in reasons["demo-private"]


def test_unknown_cost_requires_policy():
    value = candidate("demo-unknown", estimated_cost=None)
    with pytest.raises(DomainError) as caught:
        rank([value], finance=budget(allow_unknown_cost=False))
    assert caught.value.details["candidates"][0]["rejections"] == ["UNKNOWN_COST_BLOCKED"]
    with pytest.raises(DomainError):
        rank([value], policy=RoutingPolicy(unknown_cost="exclude"))
    selected = rank([value])
    assert selected.evaluations[0].estimated_cost is None


@pytest.mark.parametrize(
    "strategy,expected",
    [
        ("preferred", "demo-quality"),
        ("lowest_estimated_cost", "demo-cheap"),
        ("quality_first", "demo-quality"),
        ("balanced", "demo-cheap"),
    ],
)
def test_policy_ranking_is_deterministic(strategy, expected):
    values = [
        candidate(
            "demo-cheap",
            estimated_cost="0.10",
            quality={"grade": 3, "source_type": "manual", "source": "DEMO review"},
        ),
        candidate(
            "demo-quality",
            estimated_cost="0.60",
            quality={"grade": 5, "source_type": "benchmark", "source": "DEMO benchmark"},
        ),
    ]
    policy = RoutingPolicy(strategy=strategy, preferred_ids=["demo-quality"], minimum_quality=3)
    selected = rank(values, policy=policy)
    assert selected.selected_id == expected
    assert rank(list(reversed(values)), policy=policy) == selected


def test_balanced_applies_quality_latency_and_missing_observation_policy():
    values = [
        candidate(
            "demo-low", quality={"grade": 1, "source_type": "manual", "source": "DEMO review"}
        ),
        candidate(
            "demo-slow",
            quality={"grade": 5, "source_type": "manual", "source": "DEMO review"},
            latency={"p95_ms": 5000, "source": "DEMO measurement"},
        ),
        candidate("demo-missing"),
        candidate(
            "demo-fit",
            quality={"grade": 3, "source_type": "manual", "source": "DEMO review"},
            latency={"p95_ms": 900, "source": "DEMO measurement"},
        ),
    ]
    policy = RoutingPolicy(
        strategy="balanced",
        minimum_quality=3,
        max_latency_ms=1000,
        unknown_quality="exclude",
        unknown_latency="exclude",
    )
    assert rank(values, policy=policy).selected_id == "demo-fit"


def test_budget_uses_reserved_amount_units_and_exact_micro_costs():
    values = [
        candidate("demo-expensive", estimated_cost="0.500001"),
        candidate("demo-fit", estimated_cost="0.500000"),
    ]
    selected = rank(
        values,
        finance=budget(budget_limit="2", reserved="1"),
        requirements=RoutingRequirements(units=2),
    )
    assert selected.selected_id == "demo-fit"
    assert next(
        item for item in selected.evaluations if item.candidate_id == "demo-fit"
    ).estimated_cost == Decimal("1.000000")


def test_remote_consent_health_and_profile_enablement_are_enforced():
    with pytest.raises(DomainError):
        rank([candidate("demo-provider")], allow_remote=False)
    with pytest.raises(DomainError):
        rank([candidate("demo-provider")], finance=budget(allow_remote=False))
    with pytest.raises(DomainError):
        rank([candidate("demo-provider", health="unhealthy")])
    with pytest.raises(DomainError):
        rank([candidate("demo-provider", enabled=False)])


def test_mock_remains_usable_without_remote_consent_or_a_paid_budget():
    value = candidate("mock-director", is_mock=True, estimated_cost="0")
    assert (
        rank(
            [value],
            finance=budget(currency="CNY", budget_limit="1", spent="2", allow_remote=False),
            allow_remote=False,
        ).selected_id
        == "mock-director"
    )


def test_request_requirements_filter_timestamp_and_reference_support():
    with pytest.raises(DomainError):
        rank([candidate("demo-provider")], requirements=RoutingRequirements(timestamps=True))
    with pytest.raises(DomainError):
        rank(
            [candidate("demo-provider")],
            requirements=RoutingRequirements(reference_roles=["start_image"]),
        )
    value = candidate("demo-provider", timestamps=True, reference_roles=["start_image"])
    assert (
        rank(
            [value],
            requirements=RoutingRequirements(timestamps=True, reference_roles=["start_image"]),
        ).selected_id
        == value.id
    )


def test_quality_observations_require_sources_and_policy_ids_are_unique():
    with pytest.raises(ValidationError):
        candidate("demo-provider", quality={"grade": 5})
    with pytest.raises(ValidationError):
        RoutingPolicy(preferred_ids=["demo-a", "demo-a"])


def test_large_batch_estimates_return_a_budget_rejection():
    with pytest.raises(DomainError) as caught:
        rank(
            [candidate("demo-large", estimated_cost="1000000000")],
            requirements=RoutingRequirements(units=1000),
        )
    assert "BUDGET_EXCEEDED" in caught.value.details["candidates"][0]["rejections"]


def test_approved_scope_and_cost_bound_are_enforced():
    values = [candidate("demo-a"), candidate("demo-b", estimated_cost="0.1")]
    assert rank(values, policy=RoutingPolicy(allowed_ids=["demo-a"])).selected_id == "demo-a"
    with pytest.raises(DomainError):
        rank(values, policy=RoutingPolicy(max_estimated_cost="0.05"))


def test_remote_only_requests_exclude_mock_candidates():
    result = rank(
        [candidate("mock-director", is_mock=True, estimated_cost="0"), candidate("demo-llm")],
        requirements=RoutingRequirements(remote_only=True),
    )
    assert result.selected_id == "demo-llm"
