from eventstore import EventStore


def test_append_and_replay_order():
    store = EventStore()
    for i in range(5):
        store.append("e1", "utterance", {"i": i})
    store.append("e2", "utterance", {"i": 99})
    events = store.events("e1")
    assert [e["payload"]["i"] for e in events] == [0, 1, 2, 3, 4]
    assert all(e["event_type"] == "utterance" for e in events)


def test_unicode_payload_roundtrip():
    store = EventStore()
    store.append("e1", "utterance", {"text": "心口疼得很"})
    assert store.events("e1")[0]["payload"]["text"] == "心口疼得很"


def test_empty_encounter():
    assert EventStore().events("nope") == []
