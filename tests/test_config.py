from gachahub.core.config import ChainStore, Paths
from gachahub.core.models import HookSpec, TaskChain, TaskStep


def test_chain_roundtrip_utf8(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    store = ChainStore(paths)
    chain = TaskChain(
        name="每日/異環",
        steps=[TaskStep(name="日常", adapter="generic", params={"command": "C:/遊戲/ok.exe"})],
        pre_hooks=[HookSpec(type="mute")],
    )
    path = store.save(chain)
    assert path.name == "每日_異環.yaml"
    assert "日常" in path.read_text(encoding="utf-8")
    assert store.list() == [chain]
    store.delete(chain.name)
    assert store.list() == []
