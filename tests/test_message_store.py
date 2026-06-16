import json
import threading

import pytest
from client.message_store import MessageStore
from tests.temp_utils import make_runtime_dir, remove_runtime_dir


@pytest.fixture
def storage_dir():
    path = make_runtime_dir("message_store_")
    yield path
    remove_runtime_dir(path)


def test_get_messages_filters_private_chats_by_stable_target(storage_dir):
    store = MessageStore(str(storage_dir))

    store.add_message("alice", {
        "type": "private",
        "sender": "alice",
        "target_id": 2,
        "related_type": "private",
        "related_target": "2",
        "chat_key": "private:2",
        "content": "to bob",
    })
    store.add_message("alice", {
        "type": "private",
        "sender": "alice",
        "target_id": 3,
        "related_type": "private",
        "related_target": "3",
        "chat_key": "private:3",
        "content": "to carol",
    })
    store.add_message("alice", {
        "type": "private",
        "sender": "bob",
        "from_id": 2,
        "target_id": 2,
        "related_type": "private",
        "related_target": "2",
        "chat_key": "private:2",
        "content": "from bob",
    })

    messages = store.get_messages("alice", "private:2", limit=10)

    assert [m["content"] for m in messages] == ["to bob", "from bob"]


def test_get_messages_keeps_legacy_private_fallbacks_separate(storage_dir):
    store = MessageStore(str(storage_dir))

    store.add_message("alice", {
        "type": "private",
        "sender": "alice",
        "receiver": "bob",
        "target_id": 2,
        "content": "legacy to bob",
    })
    store.add_message("alice", {
        "type": "private",
        "sender": "alice",
        "receiver": "carol",
        "target_id": 3,
        "content": "legacy to carol",
    })
    store.add_message("alice", {
        "type": "private",
        "sender": "bob",
        "from_id": 2,
        "content": "legacy from bob",
    })

    by_id = store.get_messages("alice", "private:2", limit=10)
    by_name = store.get_messages("alice", "bob", limit=10)

    assert [m["content"] for m in by_id] == ["legacy to bob", "legacy from bob"]
    assert [m["content"] for m in by_name] == ["legacy to bob", "legacy from bob"]


def test_get_messages_does_not_put_inbound_private_message_in_self_chat(storage_dir):
    store = MessageStore(str(storage_dir))

    store.add_message("alice", {
        "type": "private",
        "sender": "bob",
        "from_id": 2,
        "receiver_id": 1,
        "content": "legacy inbound from bob",
    })

    bob_messages = store.get_messages("alice", "private:2", limit=10)
    self_messages = store.get_messages("alice", "private:1", limit=10)

    assert [m["content"] for m in bob_messages] == ["legacy inbound from bob"]
    assert self_messages == []


def test_add_message_does_not_lose_concurrent_writes(storage_dir):
    store = MessageStore(str(storage_dir))
    total = 80
    ready = threading.Barrier(total)

    def worker(idx):
        ready.wait(timeout=5)
        store.add_message("alice", {
            "type": "private",
            "msg_id": f"m-{idx}",
            "related_type": "private",
            "related_target": "2",
            "chat_key": "private:2",
            "content": f"message {idx}",
        })

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    messages = store.get_messages("alice", "private:2", limit=total + 10)
    assert len({m["msg_id"] for m in messages}) == total


def test_corrupt_user_json_is_backed_up_and_reset(storage_dir):
    user_file = storage_dir / "alice.json"
    user_file.write_text("{bad json", encoding="utf-8")
    store = MessageStore(str(storage_dir))

    data = store.load_user_data("alice")

    assert data == {
        "username": "alice",
        "contacts": [],
        "sessions": [],
        "messages": [],
    }
    backups = list(storage_dir.glob("alice.json.corrupt.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{bad json"


def test_save_user_data_writes_valid_json_atomically(storage_dir):
    store = MessageStore(str(storage_dir))
    store.save_user_data("alice", {"username": "alice", "messages": [{"content": "你好"}]})

    with open(storage_dir / "alice.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["messages"][0]["content"] == "你好"


def test_update_message_status_handles_rejected_ack(storage_dir):
    store = MessageStore(str(storage_dir))
    store.add_message("alice", {
        "type": "private",
        "msg_id": "local-1",
        "local_msg_id": "local-1",
        "status": "pending",
        "chat_key": "private:2",
        "content": "will reject",
    })

    assert store.update_message_status("alice", "local-1", "rejected") is True

    messages = store.get_messages("alice", "private:2", limit=10)
    assert messages[0]["status"] == "rejected"
