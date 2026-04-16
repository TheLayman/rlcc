import pytest
from pathlib import Path

POC_DIR = Path(__file__).parent.parent
DATA_DIR = POC_DIR / "data"

TEST_SESSION_IDS = {
    "test-session-001",
    "test-session-full-001",
    "test-session-normal-001",
}


@pytest.fixture(autouse=True)
def clean_test_transactions():
    """Remove test transactions written by receiver tests before each test run."""
    _remove_test_records("transactions")
    _remove_test_records("alerts")
    yield
    # No teardown needed — leaving data for inspection is fine


def _remove_test_records(name: str):
    path = DATA_DIR / f"{name}.jsonl"
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    filtered = [
        line for line in lines
        if line.strip() and not any(sid in line for sid in TEST_SESSION_IDS)
    ]
    path.write_text("\n".join(filtered) + ("\n" if filtered else ""))
