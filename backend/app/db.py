from sqlalchemy import URL, Engine, create_engine

from app.config import Settings


def make_engine(settings: Settings | None = None, *, schema: str | None = None) -> Engine:
    config = settings or Settings()
    if config.postgres_password is None:
        raise ValueError("Database credentials are not configured")
    url = URL.create(
        "postgresql+psycopg",
        username=config.postgres_user,
        password=config.postgres_password.get_secret_value(),
        host=config.postgres_host,
        port=config.postgres_port,
        database=config.postgres_db,
    )
    options = {"connect_timeout": 5}
    if schema is not None:
        # Test schemas are generated internally, never supplied through an API.
        if not schema.replace("_", "").isalnum():
            raise ValueError("Invalid schema identifier")
        options["options"] = f"-c search_path={schema}"
    return create_engine(url, pool_pre_ping=True, pool_timeout=2, connect_args=options)
