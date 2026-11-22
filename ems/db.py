"""Data layer: SQLite connection handling and small query helpers."""
import sqlite3
from flask import g, current_app


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(exc=None):
    """Each request is saved as a whole (NFR2): commit on success, roll back on error."""
    db = g.pop("db", None)
    if db is not None:
        if exc is None:
            db.commit()
        else:
            db.rollback()
        db.close()


def q(sql, args=(), one=False):
    rows = get_db().execute(sql, args).fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def run(sql, args=()):
    return get_db().execute(sql, args).lastrowid


def init_db():
    with current_app.open_resource("schema.sql") as f:
        get_db().executescript(f.read().decode())
    get_db().commit()
