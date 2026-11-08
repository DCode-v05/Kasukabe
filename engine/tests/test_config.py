"""Declarative Settings: parsing, full validation, aggregated errors.

The contract under test:
- load() is parse-only and aggregates ALL parse errors in one ConfigError
- validate() returns EVERY semantic error (per-field + cross-field)
- load_validated() is the boot entry point: parse + semantic in one report
- env beats .env; .env beats typed defaults
- model fields default empty (herald owns the defaults); lc_provider is gone
- meta-invariants keep the spec, the env-less allowlist, and .env.example
  in lockstep forever
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import (
    ENVLESS_FIELDS,
    ConfigError,
    SANDBOX_IDLE_TTL_DEFAULT_S,
    Settings,
    parse_bool,
)

ENGINE_ROOT = Path(__file__).resolve().parent.parent


def clear_engine_env(monkeypatch):
    """Isolate from the developer's real environment and .env."""
    for f in dataclasses.fields(Settings):
        env = f.metadata.get("env")
        if env:
            monkeypatch.delenv(env, raising=False)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    clear_engine_env(monkeypatch)
    # Point loads at an empty .env so the repo's real one cannot leak in.
    return tmp_path / "empty.env"


class TestAggregatedReport:
    def test_multiple_errors_in_one_report(self, clean_env, monkeypatch):
        monkeypatch.setenv("MAX_TOKENS", "abc")
        monkeypatch.setenv("GLOBAL_CACHE_TTL", "2h")
        monkeypatch.setenv("ACTIVITY_IDLE_TIMEOUT_S", "5000")
        with pytest.raises(ConfigError) as exc_info:
            Settings.load_validated(env_path=clean_env)
        e = exc_info.value
        text = str(e)
        assert "MAX_TOKENS='abc'" in text
        assert "GLOBAL_CACHE_TTL='2h'" in text
        assert "strictly ordered" in text
        assert "CDT_URL is required" in text
        assert "CDT_KEY is required" in text
        assert len(e.errors) == 5

    def test_parse_error_field_keeps_typed_default(self, clean_env, monkeypatch):
        monkeypatch.setenv("MAX_TOKENS", "abc")
        settings, errors = Settings.build_from_env({})
        assert settings.max_tokens == 4096
        assert any("MAX_TOKENS" in err for err in errors)

    def test_load_is_semantically_lenient(self, clean_env):
        # No CDT_URL/CDT_KEY anywhere: load() still returns a Settings.
        s = Settings.load(env_path=clean_env)
        assert s.cdt_url == ""

    def test_load_still_raises_on_parse_errors(self, clean_env, monkeypatch):
        monkeypatch.setenv("CONTENT_INGESTION_ENABLED", "1")
        with pytest.raises(ValueError, match="feature flags must be"):
            Settings.load(env_path=clean_env)

    def test_valid_config_loads(self, clean_env, monkeypatch):
        monkeypatch.setenv("CDT_URL", "http://citadel:4000")
        monkeypatch.setenv("CDT_KEY", "sk-sept-cit-test")
        s = Settings.load_validated(env_path=clean_env)
        assert s.cdt_url == "http://citadel:4000"
        assert s.validate() == []


class TestPrecedence:
    def test_env_beats_dotenv(self, clean_env, monkeypatch, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text("MAX_TOKENS=1000\n")
        monkeypatch.setenv("MAX_TOKENS", "2000")
        s = Settings.load(env_path=env_file)
        assert s.max_tokens == 2000

    def test_dotenv_beats_default(self, clean_env, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text("MAX_TOKENS=1000\n# comment\n\nCHANNEL_MAX=7\n")
        s = Settings.load(env_path=env_file)
        assert s.max_tokens == 1000
        assert s.channel_max == 7


class TestValidators:
    def check(self, **kwargs) -> list[str]:
        base = dict(cdt_url="http://c:4000", cdt_key="sk-sept-cit-x")
        base.update(kwargs)
        return Settings(**base).validate()

    def test_url_shape(self):
        assert any("CDT_URL" in e for e in self.check(cdt_url="ftp://x"))
        assert any("CDT_URL" in e for e in self.check(cdt_url="not a url"))
        assert self.check(cdt_url="https://citadel.internal:4000") == []

    def test_cdt_key_value_never_echoed(self):
        errors = Settings(cdt_url="http://c:4000", cdt_key="").validate()
        assert any("CDT_KEY is required" in e for e in errors)
        assert not any("sk-" in e for e in errors)

    def test_positive_ints(self):
        for field_name, env in [
            ("max_tokens", "MAX_TOKENS"),
            ("channel_max", "CHANNEL_MAX"),
            ("catalog_resync_interval_s", "CATALOG_RESYNC_INTERVAL_S"),
            ("sandbox_idle_ttl_s", "SANDBOX_IDLE_TTL_S"),
        ]:
            errors = self.check(**{field_name: 0})
            assert any(env in e for e in errors), field_name

    def test_nonnegative_ints(self):
        assert any("ASSET_LOG_RETENTION_DAYS" in e for e in self.check(asset_log_retention_days=-1))
        assert self.check(asset_log_retention_days=0) == []  # 0 = sweep disabled

    def test_positive_floats(self):
        assert any("CHANNEL_POLL_DEFAULT_S" in e for e in self.check(channel_poll_default_s=0.0))
        assert any("LC_BATCH_INTERVAL_HOURS" in e for e in self.check(lc_batch_interval_hours=-1.0))
        assert self.check(channel_grace_s=0.0) == []  # zero grace is allowed

    def test_enums(self):
        assert any("SUBPROCESS_SCRUB_MODE" in e for e in self.check(subprocess_scrub_mode="loose"))
        assert any("GLOBAL_CACHE_TTL" in e for e in self.check(global_cache_ttl="2h"))
        assert self.check(subprocess_scrub_mode="strict", global_cache_ttl="5m") == []

    def test_fernet_key(self):
        import base64

        good = base64.urlsafe_b64encode(b"x" * 32).decode()
        assert self.check(ad_encryption_key=good) == []
        assert any(
            "AD_ENCRYPTION_KEY" in e
            for e in self.check(ad_encryption_key=base64.urlsafe_b64encode(b"x" * 16).decode())
        )
        assert any("AD_ENCRYPTION_KEY" in e for e in self.check(ad_encryption_key="garbage!"))
        assert self.check(ad_encryption_key="") == []  # empty = feature off

    def test_cors_origins(self):
        assert self.check(cors_origins=["*"]) == []
        assert self.check(cors_origins=["https://app.september.wtf"]) == []
        assert any(
            "CORS_ORIGINS" in e for e in self.check(cors_origins=["https://a.com/path"])
        )
        assert any("CORS_ORIGINS" in e for e in self.check(cors_origins=["ftp://a.com"]))

    def test_allowed_roots_absolute(self):
        assert self.check(allowed_roots=["/data", "/tmp"]) == []
        assert any("ALLOWED_ROOTS" in e for e in self.check(allowed_roots=["relative/path"]))

    def test_optional_urls(self):
        for field_name, env in [
            ("oauth_redirect_base_url", "OAUTH_REDIRECT_BASE_URL"),
            ("september_registry_url", "SEPTEMBER_REGISTRY_URL"),
            ("september_client_metadata_url", "SEPTEMBER_CLIENT_METADATA_URL"),
        ]:
            assert self.check(**{field_name: ""}) == [], field_name
            assert any(env in e for e in self.check(**{field_name: "not-a-url"})), field_name

    def test_activity_ordering(self):
        errors = self.check(
            activity_idle_timeout_s=100,
            activity_away_timeout_s=100,
            activity_disconnect_timeout_s=200,
        )
        assert any("strictly ordered" in e for e in errors)

    def test_embedding_override_requires_dim(self):
        errors = self.check(embedding_model="custom-embed", embedding_dim=0)
        assert any("EMBEDDING_DIM" in e for e in errors)
        assert self.check(embedding_model="custom-embed", embedding_dim=768) == []

    def test_embedding_dim_alone_is_rejected(self):
        # The inverse trap: a dim override with the default model would make
        # embeddings.py silently truncate/pad every vector to the wrong
        # length. Must fail validation, not sail through.
        errors = self.check(embedding_dim=768)
        assert any("EMBEDDING_DIM" in e and "EMBEDDING_MODEL" in e for e in errors)
        # Both unset (herald default model + default dim) is fine.
        assert self.check(embedding_dim=0) == []


class TestHeraldOwnsModelDefaults:
    def test_model_fields_default_empty(self):
        s = Settings()
        for field_name in (
            "llm_model",
            "planner_model",
            "light_model",
            "dispatch_model",
            "dispatch_model_alternative",
            "fallback_model",
            "lc_model",
            "embedding_model",
        ):
            assert getattr(s, field_name) == "", field_name
        assert s.embedding_dim == 0

    def test_lc_provider_is_gone(self):
        assert not hasattr(Settings(), "lc_provider")
        assert "lc_provider" not in {f.name for f in dataclasses.fields(Settings)}


class TestParseBool:
    def test_strict_true_false_only(self):
        assert parse_bool("X", "true") is True
        assert parse_bool("X", "FALSE") is False
        for bad in ("1", "0", "yes", "no", "on", "off", ""):
            with pytest.raises(ValueError, match="feature flags must be"):
                parse_bool("X", bad)


class TestMetaInvariants:
    def test_envless_allowlist_is_exact(self):
        actual_envless = {
            f.name for f in dataclasses.fields(Settings) if not f.metadata.get("env")
        }
        assert actual_envless == set(ENVLESS_FIELDS)

    def test_env_names_are_unique(self):
        envs = [f.metadata.get("env") for f in dataclasses.fields(Settings) if f.metadata.get("env")]
        assert len(envs) == len(set(envs))

    def test_every_env_var_documented_in_env_example(self):
        example = (ENGINE_ROOT / ".env.example").read_text(encoding="utf-8")
        missing = [
            f.metadata["env"]
            for f in dataclasses.fields(Settings)
            if f.metadata.get("env") and f.metadata["env"] not in example
        ]
        assert missing == [], f".env.example is missing: {missing}"

    def test_sandbox_ttl_single_source(self):
        from src.sandbox.provider import DEFAULT_IDLE_TTL_S

        assert DEFAULT_IDLE_TTL_S == SANDBOX_IDLE_TTL_DEFAULT_S
        assert Settings().sandbox_idle_ttl_s == SANDBOX_IDLE_TTL_DEFAULT_S
