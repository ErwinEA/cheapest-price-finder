import argparse
import getpass

from pydantic import TypeAdapter, EmailStr
from sqlalchemy import select

from .db import database
from .engine_bridge import seed
from .models import User
from .security import passwords
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Administrative commands")
    parser.add_argument("command", choices=["create-admin"])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    email = str(TypeAdapter(EmailStr).validate_python(args.email)).casefold()
    password = getpass.getpass("Admin password (12–128 characters): ")
    if not 12 <= len(password) <= 128:
        parser.error("Password must be 12–128 characters")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords differ")
    engine, sessions = database(Settings().database_url)
    seed(sessions)
    with sessions.begin() as db:
        if db.scalar(select(User).where(User.email == email)):
            parser.error("User already exists; this command never silently changes an existing account")
        db.add(User(email=email, password_hash=passwords.hash(password), is_admin=True))
    engine.dispose()
    print(f"Created administrator {email}")


if __name__ == "__main__":
    main()
