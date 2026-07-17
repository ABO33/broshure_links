from app.core.config import Settings


def test_cors_origins_accept_comma_separated_environment_value(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://mobile.example, https://preview.example")

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origins == [
        "https://mobile.example",
        "https://preview.example",
    ]
