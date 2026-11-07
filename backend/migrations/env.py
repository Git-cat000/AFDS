from alembic import context
from sqlalchemy import create_engine

from app.config import Settings
from app.db import metadata

config = context.config
url = config.get_main_option("sqlalchemy.url")
if url == "sqlite:///./data/afds.db":
    url = Settings().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
