import pytest

from custos.config import PrivacyConfig
from custos.context.privacy import MASK, is_blocked, redact_secrets


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_11ABCDEFG0123456789_abcdefghijklmnop",
        "glpat-abcdefghijklmnopqrst",
        "xoxb-1234567890-abcdefghij",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyA1234567890abcdefghijklmnopqrstuv",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_known_api_keys_are_masked(secret: str) -> None:
    result = redact_secrets(f"voici la clé {secret} ok")
    assert secret not in result
    assert result == f"voici la clé {MASK} ok"


def test_private_key_block_is_masked() -> None:
    text = "avant\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaA\nAAAA\n-----END OPENSSH PRIVATE KEY-----\naprès"
    assert redact_secrets(text) == f"avant\n{MASK}\naprès"


def test_url_credentials_are_masked() -> None:
    result = redact_secrets("postgres://quentin:hunter22@localhost/db")
    assert result == f"postgres://{MASK}@localhost/db"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DB_PASSWORD=hunter2hunter2", f"DB_PASSWORD={MASK}"),
        ('api_key: "abcdef123456"', f'api_key: "{MASK}"'),
        ("--token abcdef123456", f"--token {MASK}"),
    ],
)
def test_assignments_keep_name_and_mask_value(text: str, expected: str) -> None:
    assert redact_secrets(text) == expected


def test_known_key_in_assignment_is_masked_once() -> None:
    assert redact_secrets("ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz") == (
        f"ANTHROPIC_API_KEY={MASK}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "le password est expiré depuis hier",
        "Your token expired yesterday",
        "cargo build --release",
        "def get_secret_name(): pass",
    ],
)
def test_ordinary_text_is_untouched(text: str) -> None:
    assert redact_secrets(text) == text


def test_is_blocked_by_class() -> None:
    config = PrivacyConfig(blocked_classes=["Bitwarden"])
    assert is_blocked("Bitwarden", "Coffre", config)
    assert not is_blocked("foot", "fish", config)


def test_is_blocked_by_title() -> None:
    config = PrivacyConfig(blocked_titles=["*crédit agricole*", "*Navigation privée*"])
    assert is_blocked("brave-browser", "Mes comptes - Crédit Agricole - Brave", config)
    assert is_blocked("firefox", "Nouvel onglet — Navigation privée", config)
    assert not is_blocked("brave-browser", "Python docs - Brave", config)


def test_title_pattern_without_wildcard_matches_whole_title() -> None:
    config = PrivacyConfig(blocked_titles=["Banque"])
    assert is_blocked("brave-browser", "banque", config)
    assert not is_blocked("brave-browser", "Ma Banque - Brave", config)
