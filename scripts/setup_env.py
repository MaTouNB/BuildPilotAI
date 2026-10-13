"""Create local credentials once, without displaying or replacing them."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
content = (
    (root / ".env.example")
    .read_text()
    .replace("POSTGRES_PASSWORD=", "POSTGRES_PASSWORD=" + secrets.token_urlsafe(32))
)
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing .env preserved; no credentials changed.")
else:
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
    print("Created private local .env; password was not printed.")
