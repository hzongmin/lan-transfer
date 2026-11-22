import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_INFO = b"LanTransferV3 encrypted file transfer"
_AAD = b"LanTransferV3 file payload"
STREAM_VERSION = "v4-x25519-aesgcm-stream"
_STREAM_INFO = b"LanTransferV4 encrypted stream transfer"
_STREAM_AAD = b"LanTransferV4 stream chunk"


@dataclass(frozen=True)
class EncryptionKeyPair:
    private_key: x25519.X25519PrivateKey
    public_key_b64: str


@dataclass(frozen=True)
class StreamEncryptionContext:
    key: bytes
    nonce_prefix: bytes
    ephemeral_public_key_b64: str
    nonce_prefix_b64: str


@dataclass(frozen=True)
class StreamDecryptionContext:
    key: bytes
    nonce_prefix: bytes


def generate_keypair() -> EncryptionKeyPair:
    private_key = x25519.X25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return EncryptionKeyPair(private_key=private_key, public_key_b64=_b64encode(public_key))


def encrypt_for_peer(peer_public_key_b64: str, plaintext: bytes) -> dict[str, str]:
    ephemeral = x25519.X25519PrivateKey.generate()
    peer_public = x25519.X25519PublicKey.from_public_bytes(_b64decode(peer_public_key_b64))
    key = _derive_key(ephemeral.exchange(peer_public))
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _AAD)
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "version": "v3-x25519-aesgcm",
        "ephemeralPublicKey": _b64encode(ephemeral_public),
        "nonce": _b64encode(nonce),
        "ciphertext": _b64encode(ciphertext),
    }


def decrypt_from_peer(private_key: x25519.X25519PrivateKey, payload: dict[str, str]) -> bytes:
    if payload.get("version") != "v3-x25519-aesgcm":
        raise ValueError("Unsupported encrypted payload version")
    ephemeral_public = x25519.X25519PublicKey.from_public_bytes(_b64decode(payload["ephemeralPublicKey"]))
    key = _derive_key(private_key.exchange(ephemeral_public))
    return AESGCM(key).decrypt(_b64decode(payload["nonce"]), _b64decode(payload["ciphertext"]), _AAD)


def create_stream_sender(peer_public_key_b64: str) -> StreamEncryptionContext:
    ephemeral = x25519.X25519PrivateKey.generate()
    peer_public = x25519.X25519PublicKey.from_public_bytes(_b64decode(peer_public_key_b64))
    key = _derive_stream_key(ephemeral.exchange(peer_public))
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    nonce_prefix = os.urandom(4)
    return StreamEncryptionContext(
        key=key,
        nonce_prefix=nonce_prefix,
        ephemeral_public_key_b64=_b64encode(ephemeral_public),
        nonce_prefix_b64=_b64encode(nonce_prefix),
    )


def create_stream_receiver(
    private_key: x25519.X25519PrivateKey,
    ephemeral_public_key_b64: str,
    nonce_prefix_b64: str,
) -> StreamDecryptionContext:
    ephemeral_public = x25519.X25519PublicKey.from_public_bytes(_b64decode(ephemeral_public_key_b64))
    nonce_prefix = _b64decode(nonce_prefix_b64)
    if len(nonce_prefix) != 4:
        raise ValueError("Invalid stream nonce prefix")
    return StreamDecryptionContext(
        key=_derive_stream_key(private_key.exchange(ephemeral_public)),
        nonce_prefix=nonce_prefix,
    )


def encrypt_stream_chunk(context: StreamEncryptionContext, index: int, plaintext: bytes) -> str:
    ciphertext = AESGCM(context.key).encrypt(_stream_nonce(context.nonce_prefix, index), plaintext, _stream_aad(index))
    return _b64encode(ciphertext)


def decrypt_stream_chunk(context: StreamDecryptionContext, index: int, ciphertext_b64: str) -> bytes:
    try:
        return AESGCM(context.key).decrypt(
            _stream_nonce(context.nonce_prefix, index),
            _b64decode(ciphertext_b64),
            _stream_aad(index),
        )
    except (InvalidTag, ValueError) as exc:
        raise ValueError("Invalid encrypted stream chunk") from exc


def _derive_key(shared_secret: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_INFO,
    ).derive(shared_secret)


def _derive_stream_key(shared_secret: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_STREAM_INFO,
    ).derive(shared_secret)


def _stream_nonce(nonce_prefix: bytes, index: int) -> bytes:
    if index < 0:
        raise ValueError("Chunk index must be non-negative")
    return nonce_prefix + index.to_bytes(8, "big")


def _stream_aad(index: int) -> bytes:
    return _STREAM_AAD + index.to_bytes(8, "big")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("ascii"))
