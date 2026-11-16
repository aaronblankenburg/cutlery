import os

import pytest

from cutlery import config as config_mod
from cutlery.config import ConfigError, claude_auth
from cutlery.storage import check_trees


def secret_file(tmp_path, name, value, mode=0o600):
    p = tmp_path / name
    p.write_text(value + "\n")
    os.chmod(p, mode)
    return str(p)


class TestClaudeAuth:
    def test_oauth_token_file(self, tmp_path):
        auth = claude_auth({"CLAUDE_CODE_OAUTH_TOKEN_FILE": secret_file(tmp_path, "t", "sk-ant-oat-x")})
        assert (auth.kind, auth.variable, auth.secret) == ("oauth", "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-x")

    def test_api_key_variable(self):
        auth = claude_auth({"ANTHROPIC_API_KEY": "sk-ant-api-y"})
        assert auth.kind == "api_key"
        assert auth.env() == {"ANTHROPIC_API_KEY": "sk-ant-api-y"}

    def test_none_is_refused(self):
        with pytest.raises(ConfigError, match="No Claude authentication"):
            claude_auth({})

    def test_empty_values_count_as_unset(self):
        with pytest.raises(ConfigError, match="No Claude authentication"):
            claude_auth({"ANTHROPIC_API_KEY": "  ", "CLAUDE_CODE_OAUTH_TOKEN": ""})

    def test_both_are_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="Both a Claude subscription token and an API key"):
            claude_auth({"ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN_FILE": secret_file(tmp_path, "t", "x")})

    def test_variable_and_file_for_the_same_secret_are_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="keep only one"):
            claude_auth({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_API_KEY_FILE": secret_file(tmp_path, "k", "k")})

    def test_readable_secret_file_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="chmod 600"):
            claude_auth({"ANTHROPIC_API_KEY_FILE": secret_file(tmp_path, "k", "k", 0o644)})

    def test_missing_secret_file(self, tmp_path):
        with pytest.raises(ConfigError, match="does not exist"):
            claude_auth({"ANTHROPIC_API_KEY_FILE": str(tmp_path / "nope")})


class TestConfig:
    def test_defaults_are_container_paths(self):
        cfg = config_mod.load({})
        assert (str(cfg.data_dir), str(cfg.work_dir), str(cfg.models_dir)) == ("/data", "/work", "/models")
        assert cfg.host == "127.0.0.1"

    def test_bad_port(self):
        with pytest.raises(ConfigError, match="CUTLERY_PORT"):
            config_mod.load({"CUTLERY_PORT": "eighty"})

    def test_bad_proxy_network(self):
        with pytest.raises(ConfigError, match="CUTLERY_TRUSTED_PROXIES"):
            config_mod.load({"CUTLERY_TRUSTED_PROXIES": "10.0.0.0/8,nonsense"})


class TestStorageCheck:
    def test_all_present(self, cfg):
        check_trees(cfg)

    def test_missing_tree_is_reported_not_created(self, cfg):
        (cfg.work_dir).rmdir()
        with pytest.raises(ConfigError, match="CUTLERY_WORK_DIR: .* does not exist"):
            check_trees(cfg)
        assert not cfg.work_dir.exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
    def test_read_only_tree(self, cfg):
        os.chmod(cfg.models_dir, 0o500)
        try:
            with pytest.raises(ConfigError, match="CUTLERY_MODELS_DIR: .* is not writable"):
                check_trees(cfg)
        finally:
            os.chmod(cfg.models_dir, 0o700)
