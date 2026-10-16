from __future__ import annotations

from app.local_secrets import LocalEnvironmentSecretResolver, environment_variable_for_secret_ref


def test_local_secret_ref_maps_to_non_secret_environment_name(monkeypatch) -> None:
    variable_name = environment_variable_for_secret_ref("dify/local/baseline")
    monkeypatch.setenv(variable_name, "local-key")

    assert variable_name == "AUTOPE_DEV_SECRET_DIFY_LOCAL_BASELINE"
    assert LocalEnvironmentSecretResolver().resolve("dify/local/baseline") == "local-key"


def test_local_secret_resolver_rejects_example_placeholder(monkeypatch) -> None:
    variable_name = environment_variable_for_secret_ref("dify/local/baseline")
    monkeypatch.setenv(variable_name, "replace-with-local-dify-app-api-key")

    assert LocalEnvironmentSecretResolver().resolve("dify/local/baseline") == ""
