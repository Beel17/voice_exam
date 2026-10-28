"""Issue a device or admin token at centre set-up.

Prints the token (give it to the exam PC or the admin, once) and its SHA-256 digest (put
that in config/centre.toml under device_token_sha256 or admin_token_sha256). The service
stores only digests, so a copied config file never reveals a usable token.
"""

import hashlib
import secrets


def main() -> None:
    token = secrets.token_urlsafe(32)
    print(f"token:  {token}")
    print(f"sha256: {hashlib.sha256(token.encode()).hexdigest()}")


if __name__ == "__main__":
    main()
