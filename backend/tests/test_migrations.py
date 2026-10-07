from sqlalchemy import create_engine, inspect, text

from reel_lab.db import upgrade_database


def test_upgrade_database_creates_and_reuses_current_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.sqlite3'}"

    upgrade_database(database_url)
    upgrade_database(database_url)

    engine = create_engine(database_url)
    assert "projects" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        revision = connection.execute(text("select version_num from alembic_version")).scalar_one()
    assert revision == "b1c9d7e2a403"
    assert {"user_sessions", "session_transfers"} <= set(inspect(engine).get_table_names())
    project_columns = {column["name"] for column in inspect(engine).get_columns("projects")}
    assert {
        "media_filter",
        "music_start",
        "random_seed",
        "min_clip_duration",
        "clip_reuse_mode",
    } <= project_columns
    music_columns = {column["name"] for column in inspect(engine).get_columns("music_tracks")}
    assert {"metadata_json", "sort_order"} <= music_columns
