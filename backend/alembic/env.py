from alembic import context

from app.db import make_engine
from app.models import Base

CHECKPOINTER_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in CHECKPOINTER_TABLES)


if context.is_offline_mode():
    context.configure(
        url="postgresql+psycopg://",
        target_metadata=Base.metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection, target_metadata=Base.metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = make_engine()
        try:
            with engine.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=Base.metadata,
                    include_object=include_object,
                )
                with context.begin_transaction():
                    context.run_migrations()
        finally:
            engine.dispose()
