"""Project-owned workspace reads and Engine-managed writes."""

from sqlalchemy import select

from app import db
from app.project_engine import ProjectEngine
from app.workspace_schema import WorkspaceDocument, workspace_document


def read_workspace(connection, project_id):
    row = (
        connection.execute(
            select(db.workspaces).where(db.workspaces.c.project_id == str(project_id))
        )
        .mappings()
        .first()
    )
    return {
        "project_id": str(project_id),
        "workspace_revision": row["revision"] if row else 0,
        "initialized": row is not None,
        "document": row["payload"]
        if row
        else WorkspaceDocument().model_dump(mode="json", by_alias=True),
    }


class WorkspaceService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)

    def read(self, project_id):
        with self.database.engine.connect() as connection:
            self.engine.load(connection, project_id)
            return read_workspace(connection, project_id)

    def update(self, project_id, body, author="local"):
        def apply(current, apr, connection):
            return workspace_document(body.document, apr)

        return self.engine.mutate_workspace(project_id, body.base_workspace_revision, apply, author)
