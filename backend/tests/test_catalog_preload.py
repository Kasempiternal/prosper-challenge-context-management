import json
import shutil
import time
from pathlib import Path

from agent_tools import context
from agent_tools.context import BACKEND_DIR, load_index, preload_catalogs
from scheduling.catalog_index import CatalogIndex


def test_preload_builds_each_named_catalog_once_even_with_a_concurrent_call(tmp_path, monkeypatch):
    for name in ("catalog.json", "aliases.json"):
        shutil.copy(BACKEND_DIR / "data" / name, tmp_path / name)
    catalog = (tmp_path / "catalog.json").resolve()
    agents = tmp_path / "agents"
    agents.mkdir()
    for name, body in {"a": {"catalog": str(catalog)}, "b": {"catalog": str(catalog)},
                       "c": {"catalog": str(tmp_path / "missing.json")}, "d": {"nodes": []}}.items():
        (agents / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    (agents / "e.json").write_text("not json", encoding="utf-8")

    builds: list[Path] = []
    real_load = CatalogIndex.load

    def slow_load(path, *args):
        builds.append(Path(path))
        time.sleep(0.2)
        return real_load(path, *args)

    monkeypatch.setattr(CatalogIndex, "load", slow_load)
    thread = preload_catalogs(agents, embeddings=False)
    index = load_index(catalog)
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert builds.count(catalog) == 1
    assert sorted(builds) == sorted([catalog, tmp_path / "missing.json"])
    assert load_index(catalog) is index
    assert len(index.bookable) == 760


def test_preload_embeds_every_type_and_site_for_the_embeddings_chooser(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "a.json").write_text(json.dumps({"catalog": "data/catalog.json"}), encoding="utf-8")
    embedded: list[str] = []

    class RecordingEmbedder:
        def warm(self, texts):
            embedded.extend(texts)

    monkeypatch.setattr(context, "shared_embedder", lambda: RecordingEmbedder())
    preload_catalogs(agents, embeddings=True).join(timeout=30)

    index = load_index(BACKEND_DIR / "data" / "catalog.json")
    assert len(embedded) == len(index.types) + len(index.locations) == 82 + 8
    assert "Annual Physical (Family Medicine)" in embedded
