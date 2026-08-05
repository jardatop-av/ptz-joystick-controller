from pathlib import Path


def test_python_multipart_is_required_in_requirements() -> None:
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "python-multipart>=0.0.9" in requirements.splitlines()


def test_python_multipart_is_required_in_pyproject() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"python-multipart>=0.0.9"' in pyproject
