import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from encryption_utils import (
    create_stream_receiver,
    create_stream_sender,
    decrypt_from_peer,
    decrypt_stream_chunk,
    encrypt_for_peer,
    encrypt_stream_chunk,
    generate_keypair,
)


class EncryptionUtilsTest(unittest.TestCase):
    def test_encrypt_for_peer_round_trip(self):
        receiver = generate_keypair()
        payload = encrypt_for_peer(receiver.public_key_b64, b"secret bytes")

        plaintext = decrypt_from_peer(receiver.private_key, payload)

        self.assertEqual(plaintext, b"secret bytes")
        self.assertNotIn("secret", payload["ciphertext"])

    def test_stream_encryption_round_trips_multiple_chunks(self):
        receiver = generate_keypair()
        sender = create_stream_sender(receiver.public_key_b64)
        receiver_context = create_stream_receiver(
            receiver.private_key,
            sender.ephemeral_public_key_b64,
            sender.nonce_prefix_b64,
        )

        encrypted_first = encrypt_stream_chunk(sender, 0, b"large ")
        encrypted_second = encrypt_stream_chunk(sender, 1, b"file")

        self.assertEqual(decrypt_stream_chunk(receiver_context, 0, encrypted_first), b"large ")
        self.assertEqual(decrypt_stream_chunk(receiver_context, 1, encrypted_second), b"file")

    def test_stream_encryption_rejects_wrong_chunk_index(self):
        receiver = generate_keypair()
        sender = create_stream_sender(receiver.public_key_b64)
        receiver_context = create_stream_receiver(
            receiver.private_key,
            sender.ephemeral_public_key_b64,
            sender.nonce_prefix_b64,
        )
        encrypted = encrypt_stream_chunk(sender, 0, b"chunk")

        with self.assertRaises(ValueError):
            decrypt_stream_chunk(receiver_context, 1, encrypted)


if __name__ == "__main__":
    unittest.main()
