from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def database(url):
    engine = create_engine(url, pool_pre_ping=True, **(
        {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
    ))
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine, sessionmaker(engine, expire_on_commit=False)
