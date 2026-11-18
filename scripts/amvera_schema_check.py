"""Read-only revision/schema gate. Build the reference in a disposable directory."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import ROOT


def structure(connection):
    return {(kind, name): " ".join(sql.split()) for kind, name, sql in connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    )}


def inspect_copy(path):
    path = Path(path).resolve(strict=True)
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    known = {revision.revision for revision in scripts.walk_revisions()}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        revisions = source.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(revisions) != 1 or revisions[0][0] not in known:
            raise ValueError("Unknown or ambiguous Alembic revision. Stop maintenance.")
        revision = revisions[0][0]
        if source.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or source.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database integrity check failed. Stop maintenance.")
        actual = structure(source)
    with TemporaryDirectory() as folder:
        reference = Path(folder) / "reference.db"
        config.attributes["database_url"] = "sqlite:///" + reference.as_posix()
        command.upgrade(config, revision)
        with closing(sqlite3.connect(reference)) as connection:
            expected = structure(connection)
    if actual != expected:
        raise ValueError("Schema differs from migrations at its recorded revision. Stop maintenance.")
    return {"revision": revision, "head": scripts.get_current_head(), "schema_objects": len(actual)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    print(json.dumps(inspect_copy(parser.parse_args().database)))

