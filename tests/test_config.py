from app.config import normalize_path_prefix


def test_normalize_path_prefix_empty_and_root() -> None:
    assert normalize_path_prefix("") == ""
    assert normalize_path_prefix("/") == ""


def test_normalize_path_prefix_formats_value() -> None:
    assert normalize_path_prefix("proxy") == "/proxy"
    assert normalize_path_prefix("/proxy") == "/proxy"
    assert normalize_path_prefix("/proxy/") == "/proxy"
