"""Jetons de déblocage signés (ECDSA P-256 / SHA-256).

Format : base64url(payload JSON) + "." + base64url(signature DER).
L'app Android vérifie la signature avec la clé publique embarquée
(Signature "SHA256withECDSA"), donc un faux serveur ne peut pas débloquer le téléphone.
"""

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class Signer:
    def __init__(self, key_path: Path):
        if key_path.exists():
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise RuntimeError(f"{key_path} n'est pas une clé EC")
            self._key = key
        else:
            self._key = ec.generate_private_key(ec.SECP256R1())
            key_path.write_bytes(
                self._key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            key_path.chmod(0o600)

    def public_key_b64(self) -> str:
        """Clé publique SubjectPublicKeyInfo (DER) en base64 standard, à coller dans l'app."""
        der = self._key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(der).decode()

    def sign(self, payload: dict) -> str:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        signature = self._key.sign(body, ec.ECDSA(hashes.SHA256()))
        return f"{_b64(body)}.{_b64(signature)}"

    def verify(self, token: str) -> dict:
        body_b64, sig_b64 = token.split(".")
        body = _unb64(body_b64)
        self._key.public_key().verify(_unb64(sig_b64), body, ec.ECDSA(hashes.SHA256()))
        return json.loads(body)
