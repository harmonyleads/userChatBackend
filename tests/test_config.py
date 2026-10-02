from pathlib import Path

from app.config import DatabaseSettings, Settings, apply_env
from app.db.pool import connection_params, split_sql, ssl_argument, strip_sslmode


def test_environment_overrides_configuration(monkeypatch) -> None:
    monkeypatch.setenv("WIDGET_KEYS", "alpha-widget-key-1, beta-widget-key-2")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "signing-secret-value")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@db.example:5432/postgres")
    monkeypatch.setenv("DATABASE_ENABLED", "false")
    monkeypatch.setenv("SARAH_ALLOWED_AUTH_CODES", "code-a, code-b")
    monkeypatch.setenv("PERSISTENCE_WAIT_FOR_COMPLETION", "false")

    settings = apply_env(Settings())

    assert settings.auth.widget_keys == ["alpha-widget-key-1", "beta-widget-key-2"]
    assert settings.auth.signing_secret == "signing-secret-value"
    assert settings.database.enabled is False
    assert settings.database.dsn.startswith("postgresql://")
    assert settings.database.host == ""
    assert settings.sarah.allowed_auth_codes == ["code-a", "code-b"]
    assert settings.persistence.wait_for_completion is False


def test_separate_database_fields_override_the_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_HOST", "aws-0-eu-west-1.pooler.supabase.com")
    monkeypatch.setenv("DATABASE_PORT", "5432")
    monkeypatch.setenv("DATABASE_USER", "postgres.projectref")
    monkeypatch.setenv("DATABASE_PASSWORD", "Harmony@123!")
    monkeypatch.setenv("DATABASE_NAME", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:secret@db.example:5432/other",
    )

    settings = apply_env(Settings())
    params = connection_params(settings.database)

    assert params["host"] == "aws-0-eu-west-1.pooler.supabase.com"
    assert params["port"] == 5432
    assert params["user"] == "postgres.projectref"
    assert params["password"] == "Harmony@123!"
    assert params["database"] == "postgres"


def test_ssl_require_encrypts_without_rejecting_the_pooler_chain() -> None:
    settings = DatabaseSettings(ssl=True)
    assert ssl_argument(settings, "") == "require"
    assert ssl_argument(settings, "disable") is False
    assert ssl_argument(DatabaseSettings(ssl=False), "") is False


def test_password_brackets_do_not_get_parsed_as_the_host() -> None:
    from app.db.pool import parse_postgres_dsn

    parsed = parse_postgres_dsn(
        "postgresql://postgres:[YOUR-PASSWORD]@db.pnxmxwwffwqmgrlywqlq.supabase.co:5432/postgres"
    )
    assert parsed["host"] == "db.pnxmxwwffwqmgrlywqlq.supabase.co"
    assert parsed["user"] == "postgres"
    assert parsed["password"] == "[YOUR-PASSWORD]"
    assert parsed["port"] == 5432


def test_strip_sslmode_keeps_the_rest_of_the_url() -> None:
    dsn = "postgresql://postgres:secret@db.example:5432/postgres?sslmode=require"
    assert strip_sslmode(dsn) == "postgresql://postgres:secret@db.example:5432/postgres"


def test_schema_script_contains_the_storage_tables() -> None:
    script = Path("sql/001_schema.sql").read_text(encoding="utf-8")
    statements = split_sql(script)
    text = "\n".join(statements)
    for table in ("users", "pages", "page_visits", "chat_messages"):
        assert f"create table if not exists {table}" in text
    assert "asset_name" in text
    assert "asset_version" in text
    assert "enable row level security" in text


def test_asset_version_migration_adds_the_column() -> None:
    statements = split_sql(Path("sql/003_asset_version.sql").read_text(encoding="utf-8"))
    text = "\n".join(statements)
    assert "rename column doc_version to asset_version" in text
    assert "add column if not exists asset_version" in text


def test_asset_migration_keeps_procedure_blocks_together() -> None:
    statements = split_sql(Path("sql/002_asset_fields.sql").read_text(encoding="utf-8"))
    blocks = [statement for statement in statements if statement.lower().startswith("do ")]
    assert len(blocks) == 2
    assert all(statement.rstrip().endswith("$$") for statement in blocks)
