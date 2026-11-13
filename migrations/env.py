"""Run migrations against the explicitly configured PostgreSQL database."""

from alembic import context

from platform_service.database import configured_engine


engine = context.config.attributes.get("engine")
owns_engine = engine is None
if owns_engine:
    engine = configured_engine()
try:
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
finally:
    if owns_engine:
        engine.dispose()
