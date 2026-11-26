"""Clue Finder puzzle encryption + signing (shared by the app build and the online pipeline).

File format (same for the bundled asset and the online file):

    envelope = {"payload": base64(payload_json_bytes), "sig": base64(DER ECDSA-P256-SHA256(payload_json_bytes))}
    payload  = {"format": 1, "generated": <unix seconds>, "puzzles": [entry, ...]}
    entry    = {"day": N, "rev": R, "id": "...", "n": base64(12-byte nonce), "c": base64(AES-256-GCM ciphertext+tag)}

Day N is the 1-based puzzle number (day 1 = 2026-11-01). Each day has its own AES key:
    key(N) = HMAC-SHA256(master_key, "cf-day-" + N)
and the AAD is "cf:" + N + ":" + R, so an entry cannot be moved to another day.
The plaintext is one puzzle as JSON ({"id","category","difficulty","content":{"fa":{...},"en":{...}}}).

The app ships the master key (needed to stay playable offline for a year) and the
signing PUBLIC key only. It refuses to decrypt days in the future and rejects any
file whose signature does not verify.
"""
import base64, hashlib, hmac, json, os, time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

b64 = lambda b: base64.b64encode(b).decode()
unb64 = base64.b64decode


def day_key(master: bytes, day: int) -> bytes:
    return hmac.new(master, f"cf-day-{day}".encode(), hashlib.sha256).digest()


def aad(day: int, rev: int) -> bytes:
    return f"cf:{day}:{rev}".encode()


def encrypt_puzzle(master: bytes, day: int, rev: int, puzzle: dict) -> dict:
    nonce = os.urandom(12)
    pt = json.dumps(puzzle, ensure_ascii=False, separators=(",", ":")).encode()
    ct = AESGCM(day_key(master, day)).encrypt(nonce, pt, aad(day, rev))
    return {"day": day, "rev": rev, "id": puzzle["id"], "n": b64(nonce), "c": b64(ct)}


def decrypt_entry(master: bytes, e: dict) -> dict:
    pt = AESGCM(day_key(master, e["day"])).decrypt(unb64(e["n"]), unb64(e["c"]), aad(e["day"], e["rev"]))
    return json.loads(pt)


def load_private_key(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def public_key_b64(priv) -> str:
    return b64(priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))


def sign_envelope(priv, entries: list) -> dict:
    payload = {"format": 1, "generated": int(time.time()), "puzzles": sorted(entries, key=lambda e: e["day"])}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    sig = priv.sign(raw, ec.ECDSA(hashes.SHA256()))
    return {"payload": b64(raw), "sig": b64(sig)}


def verify_envelope(pub_der_b64: str, env: dict) -> dict:
    pub = serialization.load_der_public_key(unb64(pub_der_b64))
    raw = unb64(env["payload"])
    pub.verify(unb64(env["sig"]), raw, ec.ECDSA(hashes.SHA256()))
    return json.loads(raw)


def new_keys():
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return b64(os.urandom(32)), pem
