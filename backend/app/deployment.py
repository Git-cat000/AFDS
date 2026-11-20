"""Explicit migration/serve commands and bounded deployment health checks."""

import argparse
import json
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import ROOT, Settings
from app.db import Database
from app.errors import DomainError
from app.worker_registry import WorkerRegistry


def migration_head():
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def readiness(database):
    state = {"ready": False, "schema": False, "storage": False}
    try:
        with database.engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        state["schema"] = version == migration_head()
        state["storage"] = WorkerRegistry(database).probe_storage()["status"] == "available"
        state["ready"] = (
            all((state["schema"], state["storage"]))
            and not (database.settings.data_dir / ".restore-incomplete").exists()
        )
    except (SQLAlchemyError, OSError, DomainError):
        pass
    return state


def main():
    parser = argparse.ArgumentParser(description="AFDS deployment operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("health")
    worker = commands.add_parser("worker-health")
    worker.add_argument("--node-id", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    backup.add_argument("--stopped", action="store_true", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("--stopped", action="store_true", required=True)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "serve":
        import uvicorn

        from app.main import create_app

        uvicorn.run(
            create_app(settings),
            host=args.host,
            port=args.port,
            proxy_headers=settings.production,
            forwarded_allow_ips=settings.trusted_proxy if settings.production else "127.0.0.1",
        )
        return
    if args.command == "restore":
        from app.system_backup import restore_system

        restore_system(settings, args.source, stopped=args.stopped)
        print(
            "Restored AFDS database and verified store; "
            "restore provider key separately before start."
        )
        return
    database = Database(settings)
    try:
        if args.command == "migrate":
            database.migrate()
            WorkerRegistry(database).initialize_store()
            print("AFDS migration and shared storage ready")
        elif args.command == "health":
            state = readiness(database)
            print(json.dumps(state))
            if not state["ready"]:
                sys.exit(1)
        elif args.command == "worker-health":
            nodes = WorkerRegistry(database).list()
            if not any(
                node["id"] == args.node_id and node["status"] in {"online", "draining"}
                for node in nodes
            ):
                sys.exit(1)
        else:
            from app.system_backup import backup_system

            manifest = backup_system(database, args.destination, stopped=args.stopped)
            print(f"Backed up {len(manifest['files'])} store files; provider key excluded")
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
