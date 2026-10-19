"""Repository identity keys used to recognise the same Git repository under different spellings."""
import pytest

from app.utils.repository_identity import repository_identity

GITHUB_KEY = ("address", "github.com", "", "Owner/Repo")


class TestSameRepositoryDifferentSpelling:
    @pytest.mark.parametrize(
        "spelling",
        [
            "git@github.com:Owner/Repo.git",
            "ssh://git@GitHub.com/Owner/Repo",
            "https://github.com/Owner/Repo/",
            "https://GitHub.com/Owner/Repo.git",
            "github.com/Owner/Repo",
            "  github.com/Owner/Repo  ",
        ],
    )
    def test_spellings_of_one_repository_share_a_key(self, spelling):
        assert repository_identity(spelling) == GITHUB_KEY

    @pytest.mark.parametrize("spelling", ["owner/repo", "owner/repo.git", "owner/repo/", " owner/repo "])
    def test_host_less_owner_and_repo_share_a_path_key(self, spelling):
        assert repository_identity(spelling) == ("path", "owner/repo")


class TestDifferentRepositories:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("https://github.com/Owner/Repo", "https://github.com/owner/repo"),
            ("https://github.com/Owner/Repo", "https://gitlab.com/Owner/Repo"),
            ("https://host:8443/owner/repo", "https://host/owner/repo"),
            ("ssh://git@host:2222/owner/repo", "ssh://git@host/owner/repo"),
            ("owner/repo", "other/repo"),
        ],
        ids=["path-case", "host", "https-port", "ssh-port", "owner"],
    )
    def test_keys_differ_where_path_host_or_port_differ(self, left, right):
        assert repository_identity(left) != repository_identity(right)

    def test_explicit_port_is_part_of_the_key(self):
        assert repository_identity("https://host:8443/owner/repo") == ("address", "host", "8443", "owner/repo")


class TestUninterpretableAddresses:
    @pytest.mark.parametrize(
        "address",
        [
            "owner",
            "owner/../repo",
            "ftp://host/owner/repo",
            "https:///owner/repo",
            "https://user:secret@host/owner/repo",
            "https://user@host/owner/repo",
            "https://host:notaport/owner/repo",
            "https://host/owner/../repo",
            "https://host/owner/%2e%2e/repo",
            "https://host",
            "with space/repo",
            "C:\\path\\repo",
        ],
    )
    def test_address_is_kept_as_a_literal_key(self, address):
        assert repository_identity(address) == ("literal", address)

    def test_literal_key_ignores_surrounding_whitespace_only(self):
        assert repository_identity("  owner  ") == ("literal", "owner")

    def test_ssh_user_is_allowed_where_other_schemes_refuse_it(self):
        assert repository_identity("ssh://git@host/owner/repo") == ("address", "host", "", "owner/repo")
