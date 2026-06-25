import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


_SALT = b"telegram_vault_salt_v1"


def _derive_key(master_password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(master_password.encode()))


def get_fernet(master_password: str) -> Fernet:
    return Fernet(_derive_key(master_password))


def encrypt(master_password: str, plaintext: str) -> str:
    return get_fernet(master_password).encrypt(plaintext.encode()).decode()


def decrypt(master_password: str, token: str) -> str:
    return get_fernet(master_password).decrypt(token.encode()).decode()
