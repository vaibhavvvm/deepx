from autodev.sandbox.image_select import (
    IMAGE_JAVA,
    IMAGE_NODE,
    IMAGE_POLYGLOT,
    IMAGE_PYTHON,
    select_image,
)


def test_python_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    assert select_image(tmp_path).image == IMAGE_PYTHON


def test_node_repo(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert select_image(tmp_path).image == IMAGE_NODE


def test_java_repo(tmp_path):
    (tmp_path / "pom.xml").write_text("")
    assert select_image(tmp_path).image == IMAGE_JAVA


def test_polyglot_repo(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pom.xml").write_text("")
    sel = select_image(tmp_path)
    assert sel.image == IMAGE_POLYGLOT
    assert set(["node", "java"]) <= set(sel.detected_stacks)


def test_empty_repo_polyglot(tmp_path):
    assert select_image(tmp_path).image == IMAGE_POLYGLOT


def test_override_wins(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    sel = select_image(tmp_path, override="myimage:tag")
    assert sel.image == "myimage:tag"
