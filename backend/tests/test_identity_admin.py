import sys

import pytest
from sqlalchemy import select

from app import admin, db
from app.identity_service import IdentityService


def test_interactive_admin_creation_preserves_password_and_claims_projects(
    dialect_database, monkeypatch
):
    database = dialect_database
    monkeypatch.setattr(admin, "Settings", lambda: database.settings)
    answers = iter(["  DEMO admin password  "] * 2)
    monkeypatch.setattr(admin.getpass, "getpass", lambda _: next(answers))
    monkeypatch.setattr(sys, "argv", ["app.admin", "create-user", "demo-admin", "--admin"])
    admin.main()
    with database.transaction() as connection:
        account = (
            connection.execute(select(db.users).where(db.users.c.username == "demo-admin"))
            .mappings()
            .one()
        )
    assert account["is_admin"]
    service = IdentityService(database)
    assert service.login("demo-admin", "  DEMO admin password  ", "DEMO peer")[0]["is_admin"]
    monkeypatch.setattr(sys, "argv", ["app.admin", "claim-local-projects", "--owner", "demo-admin"])
    admin.main()


def test_admin_rejects_password_mismatch_without_creating_account(dialect_database, monkeypatch):
    database = dialect_database
    monkeypatch.setattr(admin, "Settings", lambda: database.settings)
    answers = iter(["DEMO password one", "DEMO password two"])
    monkeypatch.setattr(admin.getpass, "getpass", lambda _: next(answers))
    monkeypatch.setattr(sys, "argv", ["app.admin", "create-user", "demo-mismatch", "--admin"])
    with pytest.raises(SystemExit, match="1"):
        admin.main()
    with database.transaction() as connection:
        assert not connection.execute(
            select(db.users).where(db.users.c.username == "demo-mismatch")
        ).first()
