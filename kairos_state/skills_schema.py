"""Catálogos e assets de skills fixados por sessão."""

SKILLS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_catalogs (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    home_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    system_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_catalog_assets (
    session_id TEXT NOT NULL REFERENCES skill_catalogs(session_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    reference TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY(session_id, name, reference)
);
"""
