"""Generate a local environment without storing a shared/default JWT secret."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
destination = root / ".env"
if destination.exists():
    raise SystemExit(".env already exists; left unchanged")
template = (root / ".env.example").read_text()
destination.write_text(template.replace("JWT_SECRET=\n", f"JWT_SECRET={secrets.token_urlsafe(48)}\n", 1))
destination.chmod(0o600)
print("Created .env with a fresh secret. Fixture mode is enabled.")
