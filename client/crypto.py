"""
客户端 E2EE 端到端加密模块
使用 RSA-2048 + AES-256-GCM 混合加密方案

流程:
1. 每个用户注册时生成 RSA-2048 密钥对
2. 公钥上传到服务器，私钥本地保存
3. 发送消息: 随机 AES-256 密钥加密内容 → RSA 加密 AES 密钥
4. 接收消息: RSA 解密 AES 密钥 → AES-GCM 解密内容
"""

import os
import json
import base64
from typing import Tuple, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class E2EECrypto:
    """端到端加密处理器"""

    KEY_SIZE = 2048
    AES_KEY_SIZE = 32  # AES-256

    @staticmethod
    def generate_key_pair() -> Tuple[bytes, bytes]:
        """
        生成 RSA-2048 密钥对

        Returns:
            (private_key_pem, public_key_pem) 均为 PEM 格式 bytes
        """
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=E2EECrypto.KEY_SIZE,
        )

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        return private_pem, public_pem

    @staticmethod
    def encrypt_message(plaintext: str, recipient_pubkey_pem: bytes) -> str:
        """
        加密消息（混合加密）

        1. 生成随机 AES-256 密钥
        2. 用 AES-GCM 加密消息内容
        3. 用 RSA 加密 AES 密钥
        4. 返回 JSON 字符串

        Args:
            plaintext: 明文消息
            recipient_pubkey_pem: 接收者公钥 PEM

        Returns:
            JSON 字符串: {aes_key_enc, nonce, ciphertext, tag}
        """
        # 加载接收者公钥
        public_key = serialization.load_pem_public_key(recipient_pubkey_pem)

        # 1. 生成随机 AES-256 密钥
        aes_key = AESGCM.generate_key(bit_length=256)

        # 2. AES-GCM 加密
        aesgcm = AESGCM(aes_key)
        nonce = os.urandom(12)  # 96-bit nonce
        plaintext_bytes = plaintext.encode("utf-8")
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext_bytes, None)

        # AES-GCM 输出 = ciphertext + tag(16B)
        ciphertext = ciphertext_with_tag[:-16]
        tag = ciphertext_with_tag[-16:]

        # 3. RSA 加密 AES 密钥
        aes_key_encrypted = public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        # 4. 序列化为 JSON
        result = {
            "aes_key_enc": base64.b64encode(aes_key_encrypted).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        }

        return json.dumps(result, separators=(",", ":"))

    @staticmethod
    def decrypt_message(encrypted_json: str, private_key_pem: bytes) -> str:
        """
        解密消息（反向操作）

        Args:
            encrypted_json: encrypt_message 输出的 JSON 字符串
            private_key_pem: 接收者私钥 PEM

        Returns:
            解密后的明文消息
        """
        # 加载私钥
        private_key = serialization.load_pem_private_key(
            private_key_pem,
            password=None,
        )

        # 1. 解析 JSON
        data = json.loads(encrypted_json)
        aes_key_enc = base64.b64decode(data["aes_key_enc"])
        nonce = base64.b64decode(data["nonce"])
        ciphertext = base64.b64decode(data["ciphertext"])
        tag = base64.b64decode(data["tag"])

        # 2. RSA 解密 AES 密钥
        aes_key = private_key.decrypt(
            aes_key_enc,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        # 3. AES-GCM 解密
        aesgcm = AESGCM(aes_key)
        ciphertext_with_tag = ciphertext + tag
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)

        return plaintext_bytes.decode("utf-8")

    @staticmethod
    def encrypt_file_chunk(
        chunk: bytes,
        aes_key: bytes,
        nonce: bytes,
    ) -> bytes:
        """加密单个文件块，返回 encrypted_chunk。调用方需确保每次使用不同的 nonce"""
        aesgcm = AESGCM(aes_key)
        return aesgcm.encrypt(nonce, chunk, None)

    @staticmethod
    def decrypt_file_chunk(
        encrypted_chunk: bytes,
        aes_key: bytes,
        nonce: bytes,
    ) -> bytes:
        """解密单个文件块"""
        aesgcm = AESGCM(aes_key)
        return aesgcm.decrypt(nonce, encrypted_chunk, None)

    @staticmethod
    def generate_file_key() -> bytes:
        """生成用于文件加密的 AES-256 密钥"""
        return AESGCM.generate_key(bit_length=256)


def save_private_key(private_key_pem: bytes, filepath: str):
    """保存私钥到文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(private_key_pem)


def load_private_key(filepath: str) -> Optional[bytes]:
    """从文件加载私钥"""
    try:
        with open(filepath, "rb") as f:
            return f.read()
    except (FileNotFoundError, IOError):
        return None
