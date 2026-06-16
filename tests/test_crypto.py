import json

import pytest

crypto_module = pytest.importorskip("client.crypto", reason="cryptography is required")
pytest.importorskip("cryptography", reason="cryptography is required")

E2EECrypto = crypto_module.E2EECrypto


def test_rsa_aes_message_roundtrip():
    private_key, public_key = E2EECrypto.generate_key_pair()
    plaintext = "端到端加密消息：hello 123"

    encrypted = E2EECrypto.encrypt_message(plaintext, public_key)
    decrypted = E2EECrypto.decrypt_message(encrypted, private_key)

    assert decrypted == plaintext


def test_encrypted_message_payload_shape():
    private_key, public_key = E2EECrypto.generate_key_pair()

    encrypted = E2EECrypto.encrypt_message("shape check", public_key)
    payload = json.loads(encrypted)

    assert set(payload) == {"aes_key_enc", "nonce", "ciphertext", "tag"}
    assert E2EECrypto.decrypt_message(encrypted, private_key) == "shape check"


def test_aes_file_chunk_roundtrip():
    key = E2EECrypto.generate_file_key()
    nonce = b"0" * 12
    chunk = b"file-bytes-001" * 128

    encrypted = E2EECrypto.encrypt_file_chunk(chunk, key, nonce)
    decrypted = E2EECrypto.decrypt_file_chunk(encrypted, key, nonce)

    assert decrypted == chunk
    assert encrypted != chunk
