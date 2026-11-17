"""Interactive account setup and explicit legacy project ownership assignment."""

import argparse
import getpass

from app.config import Settings
from app.db import Database
from app.errors import DomainError
from app.identity_service import IdentityService


def main():
    parser = argparse.ArgumentParser(description="Manage AFDS application accounts")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user")
    create.add_argument("username", nargs="?")
    create.add_argument("--admin", action="store_true")
    create.add_argument("--display-name")
    claim = commands.add_parser("claim-local-projects")
    claim.add_argument("--owner", required=True)
    arguments = parser.parse_args()
    database = Database(Settings())
    try:
        database.migrate()
        service = IdentityService(database)
        if arguments.command == "create-user":
            username = arguments.username or input("Username ").strip()
            password = getpass.getpass("Password ")
            if password != getpass.getpass("Repeat password "):
                raise DomainError("PASSWORD_MISMATCH", "The two passwords differ", 422)
            user = service.create_user(
                username, password, is_admin=arguments.admin, display_name=arguments.display_name
            )
            print(f"Created {user['username']} ({user['id']})")
        else:
            identifiers = service.claim_local_projects(arguments.owner)
            print(f"Assigned {len(identifiers)} legacy projects to {arguments.owner}")
    except DomainError as error:
        parser.exit(1, f"{error.code} {error.message}\n")
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
