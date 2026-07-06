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


def test_all_events_global_replay():
    """全量回放 API: 跨 encounter 按全局序返回。"""
    from eventstore import EventStore

    store = EventStore()
    store.append("enc_a", "utterance", {"text": "a1"})
    store.append("enc_b", "utterance", {"text": "b1"})
    store.append("enc_a", "utterance", {"text": "a2"})
    allev = store.all_events()
    assert [e["encounter_id"] for e in allev] == ["enc_a", "enc_b", "enc_a"]
    assert [e["seq"] for e in allev] == sorted(e["seq"] for e in allev)
    assert store.encounter_ids() == ["enc_a", "enc_b"]
