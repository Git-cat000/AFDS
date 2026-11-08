"""Atomic provider cost reservations; estimates never masquerade as actual charges."""

import time
from decimal import Decimal
from typing import Annotated

from pydantic import Field
from sqlalchemy import insert, select, update

from app import db
from app.errors import DomainError
from app.project_engine import ProjectEngine, emit
from app.schemas import RevisionRequest, new_id

Money = Annotated[Decimal, Field(ge=0, le=1_000_000_000, decimal_places=6)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


def micros(amount):
    return int(Decimal(str(amount)) * 1_000_000)


def money(value):
    return f"{Decimal(value) / 1_000_000:.6f}"


class BudgetUpdate(RevisionRequest):
    budget_limit: Money
    currency: Currency = "USD"
    allow_unknown_cost: bool = True
    allow_remote: bool = True


class Reconcile(RevisionRequest):
    actual_amount: Money
    currency: Currency


class BudgetService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)

    def entries(self, connection, project_id):
        return [
            dict(r)
            for r in connection.execute(
                select(db.cost_entries).where(db.cost_entries.c.project_id == str(project_id))
            ).mappings()
        ]

    def summary(self, connection, apr):
        policy = apr["budget"]
        entries = self.entries(connection, apr["project_id"])
        spent = sum(e["payload"].get("actual_micros", 0) or 0 for e in entries)
        reserved = sum(
            (e["payload"].get("estimate_micros") or 0)
            for e in entries
            if e["payload"]["state"] in {"reserved", "pending"}
        )
        unknown = sum(
            e["payload"]["state"] in {"reserved", "pending"}
            and e["payload"].get("estimate_micros") is None
            for e in entries
        )
        limit = micros(policy.get("budget_limit", 0))
        public_entries = []
        for entry in entries:
            item = entry["payload"]
            public_entries.append(
                {
                    "id": entry["id"],
                    "reference": entry["reference"],
                    **item,
                    "estimate": money(item["estimate_micros"])
                    if item["estimate_micros"] is not None
                    else None,
                    "actual": money(item["actual_micros"])
                    if item.get("actual_micros") is not None
                    else None,
                }
            )
        return {
            "budget_limit": money(limit),
            "currency": policy.get("currency", "USD"),
            "allow_unknown_cost": policy.get("allow_unknown_cost", True),
            "allow_remote": policy.get("allow_remote", True),
            "spent": money(spent),
            "reserved": money(reserved),
            "unknown_count": unknown,
            "over_budget": spent + reserved > limit,
            "entries": public_entries,
            "revision": apr["revision"],
        }

    def public(self, project_id):
        with self.database.engine.connect() as connection:
            return self.summary(connection, self.engine.load(connection, project_id))

    def update(self, project_id, body):
        def apply(apr, connection):
            state = self.summary(connection, apr)
            if body.currency != state["currency"] and state["entries"]:
                raise DomainError(
                    "BUDGET_CURRENCY_LOCKED", "Currency is fixed after the first charge", 409
                )
            apr["budget"].update(
                body.model_dump(mode="json", exclude={"base_revision", "approved"})
            )

        return self.engine.mutate(
            project_id, body.base_revision, "Update budget and privacy policy", apply
        )

    def enforce(self, apr, config):
        if not apr["budget"].get("allow_remote", True):
            raise DomainError(
                "PROJECT_REMOTE_BLOCKED", "Project disallows external model requests", 409
            )
        if config.get("request_cost") is None and not apr["budget"].get("allow_unknown_cost", True):
            raise DomainError(
                "UNKNOWN_COST_BLOCKED", "Set a cost estimate or allow unknown charges", 409
            )

    def reserve(self, connection, apr, reference, config):
        self.enforce(apr, config)
        estimate = config.get("request_cost")
        currency = config.get("cost_currency", "USD")
        state = self.summary(connection, apr)
        if currency != state["currency"]:
            raise DomainError(
                "BUDGET_CURRENCY_MISMATCH", "Provider estimate currency differs from project", 409
            )
        prior = (
            connection.execute(
                select(db.cost_entries).where(db.cost_entries.c.reference == reference)
            )
            .mappings()
            .first()
        )
        if prior and prior["payload"]["state"] != "released":
            return prior["id"]
        amount = micros(estimate) if estimate is not None else None
        if amount is not None and amount + micros(state["spent"]) + micros(
            state["reserved"]
        ) > micros(state["budget_limit"]):
            raise DomainError("BUDGET_EXCEEDED", "Request exceeds remaining project budget", 409)
        payload = {
            "state": "reserved",
            "estimate_micros": amount,
            "actual_micros": None,
            "currency": currency,
            "provider_id": config["provider_id"],
            "created_at": time.time(),
        }
        entry_id = prior["id"] if prior else new_id()
        if prior:
            connection.execute(
                update(db.cost_entries)
                .where(db.cost_entries.c.id == entry_id)
                .values(payload=payload)
            )
        else:
            connection.execute(
                insert(db.cost_entries).values(
                    id=entry_id, project_id=apr["project_id"], reference=reference, payload=payload
                )
            )
        emit(connection, apr["project_id"], "budget.reserved", {"entry_id": entry_id})
        return entry_id

    def finalize(self, connection, entry_id, submitted):
        if not entry_id:
            return
        row = (
            connection.execute(select(db.cost_entries).where(db.cost_entries.c.id == entry_id))
            .mappings()
            .one()
        )
        if row["payload"]["state"] != "reserved":
            return
        payload = {**row["payload"], "state": "pending" if submitted else "released"}
        connection.execute(
            update(db.cost_entries).where(db.cost_entries.c.id == entry_id).values(payload=payload)
        )
        emit(
            connection,
            row["project_id"],
            "budget.updated",
            {"entry_id": entry_id, "state": payload["state"]},
        )

    def reconcile(self, project_id, entry_id, body):
        if not body.approved:
            raise DomainError(
                "APPROVAL_REQUIRED", "Confirm the actual charge against provider billing", 409
            )

        def apply(apr, connection):
            row = (
                connection.execute(
                    select(db.cost_entries).where(
                        db.cost_entries.c.id == str(entry_id),
                        db.cost_entries.c.project_id == str(project_id),
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise DomainError("NOT_FOUND", "Charge does not belong to project", 404)
            payload = row["payload"]
            if payload["state"] != "pending":
                raise DomainError(
                    "CHARGE_NOT_PENDING",
                    "Only terminal pending charges can be reconciled once",
                    409,
                )
            if body.currency != payload["currency"]:
                raise DomainError(
                    "BUDGET_CURRENCY_MISMATCH", "Use the original charge currency", 409
                )
            payload = {
                **payload,
                "state": "settled",
                "actual_micros": micros(body.actual_amount),
                "settled_at": time.time(),
                "source": "user_reconciled",
            }
            connection.execute(
                update(db.cost_entries)
                .where(db.cost_entries.c.id == str(entry_id))
                .values(payload=payload)
            )
            self.engine.approve(
                connection,
                project_id,
                "cost_reconciliation",
                {
                    "entry_id": str(entry_id),
                    "actual_amount": str(body.actual_amount),
                    "currency": body.currency,
                },
            )

        return self.engine.mutate(
            project_id, body.base_revision, "Reconcile provider charge", apply
        )
