import math
from collections import Counter

import pytest

from nemo.memory import MemoryStore
from nemo.memory.consolidate import dream, entities, keeps_entities, propose
from nemo.memory.tokenize import idf, jaccard, token_set, tokens


def test_tokenizer_words_and_cjk_bigrams():
    assert tokens("I like Café au lait") == ["i", "like", "café", "au", "lait"]
    assert tokens("我喜欢咖啡") == ["我喜", "喜欢", "欢咖", "咖啡"]
    assert tokens("猫") == ["猫"]
    assert tokens("user_name") == ["user", "name"]  # underscore splits words
    assert tokens("Привет мир") == ["привет", "мир"]
    assert "咖啡" in tokens("I drink 咖啡 daily")


def test_idf_formula():
    df = Counter({"tea": 1, "the": 9})
    n = 10
    assert idf("tea", df, n) == pytest.approx(math.log(1 + (10 - 1 + 0.5) / (1 + 0.5)))
    assert idf("tea", df, n) > idf("the", df, n)  # rare words weigh more
    assert idf("unseen", df, n) > idf("tea", df, n)


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    yield s
    s.close()


def test_rare_word_ranking(store):
    store.add("The user likes coffee in the morning", "preference")
    store.add("The user works at Zhejiang University", "work")
    store.add("The user likes the color green", "preference")
    hits = store.search("which university does the user go to?")
    assert hits[0].content == "The user works at Zhejiang University"
    hits = store.search("咖啡 coffee")
    assert "coffee" in hits[0].content


def test_cjk_search(store):
    store.add("用户喜欢喝咖啡", "preference")
    store.add("用户住在杭州", "identity")
    assert store.search("杭州天气")[0].content == "用户住在杭州"


def test_duplicate_add_returns_existing(store):
    a = store.add("Likes tea")
    assert store.add("likes tea").id == a.id
    assert len(store.all()) == 1


def test_every_change_can_be_undone(store):
    a = store.add("Lives in Berlin", "identity")
    store.update(a.id, content="Lives in Hangzhou")
    assert store.get(a.id).content == "Lives in Hangzhou"
    rewrite = store.changes(1)[0]
    assert rewrite.action == "rewrite"
    assert store.undo(rewrite.id)
    assert store.get(a.id).content == "Lives in Berlin"
    assert not store.undo(rewrite.id)  # only once

    store.forget(a.id)
    assert store.get(a.id) is None
    assert store.undo(store.changes(1)[0].id)
    assert store.get(a.id).content == "Lives in Berlin"


def test_merge_and_undo(store):
    a = store.add("Has a cat named Mochi")
    b = store.add("Has a cat named Mochi who is 3 years old")
    merged = store.merge([a.id, b.id], "Has a cat named Mochi, 3 years old")
    assert [m.content for m in store.all()] == ["Has a cat named Mochi, 3 years old"]
    store.undo(store.changes(1)[0].id)
    assert {m.id for m in store.all()} == {a.id, b.id}
    assert store.get(merged.id) is None


def test_entities_guard():
    assert {"mochi", "3"} <= entities("Has a cat named Mochi who is 3")
    assert "has" not in entities("Has a cat named Mochi")
    assert keeps_entities(["Flies to Tokyo on 2026-11-03"], "Flying to Tokyo on 2026-11-03")
    assert not keeps_entities(["Flies to Tokyo on 2026-11-03"], "Has a trip soon")


async def test_dream_drops_redundant_duplicates(store):
    store.add("The user likes green tea")
    store.add("The user likes green tea in the morning")
    store.add("Works on a robotics project")
    report = await dream(store)
    assert [r["action"] for r in report] == ["drop"]
    assert len(store.all()) == 2
    # one-click undo
    assert store.undo(report[0]["change_id"]) and len(store.all()) == 3


async def test_dream_merge_rejected_when_entities_lost(store):
    a, b = "Meeting with Alice on Friday at 10 in the big room", "Meeting with Alice on Friday at 10 in room 4"
    store.add(a)
    store.add(b)
    assert jaccard(token_set(a), token_set(b)) > 0.6

    async def lossy(prompt: str) -> str:
        return '{"action": "merge", "content": "Meeting with Alice on Friday"}'

    assert await propose(store, lossy) == []

    async def faithful(prompt: str) -> str:
        return '{"action": "merge", "content": "Meeting with Alice on Friday at 10 in the big room 4"}'

    proposals = await propose(store, faithful)
    assert proposals and proposals[0].action == "merge"
    report = await dream(store, faithful)
    assert [m.content for m in store.all()] == ["Meeting with Alice on Friday at 10 in the big room 4"]
    store.undo(report[0]["change_id"])
    assert len(store.all()) == 2


def test_semantic_fusion(tmp_path):
    vocab = ["pet", "animal", "cat", "kitten", "car"]

    def embed(texts):
        return [[1.0 if w in t.lower() else 0.0 for w in vocab] for t in texts]

    s = MemoryStore(tmp_path / "e.db", embedder=embed, embed_model="toy")
    s.add("Owns a kitten")
    s.add("Drives an old car")
    # no shared words with the query, but the toy embedding links kitten ~ kitten
    assert s.search("kitten photos")[0].content == "Owns a kitten"
    s.close()
