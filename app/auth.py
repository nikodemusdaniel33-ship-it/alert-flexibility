import hashlib
import hmac
import time

from fastapi import Cookie, Depends, HTTPException, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

SESSION_COOKIE = "session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600
_TELEGRAM_AUTH_MAX_AGE_SECONDS = 24 * 3600

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="alert-flexibility-session")


class TelegramAuthError(Exception):
    pass


def verify_telegram_auth(data: dict) -> dict:
    """Verify a Telegram Login Widget payload per Telegram's documented
    algorithm: https://core.telegram.org/widgets/login#checking-authorization
    """
    if not settings.telegram_bot_token:
        raise TelegramAuthError("TELEGRAM_BOT_TOKEN is not configured")

    received_hash = data.get("hash")
    if not received_hash:
        raise TelegramAuthError("missing hash")

    check_fields = {k: v for k, v in data.items() if k != "hash"}
    data_check_string = "\n".join(f"{k}={check_fields[k]}" for k in sorted(check_fields))

    secret_key = hashlib.sha256(settings.telegram_bot_token.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise TelegramAuthError("hash mismatch")

    auth_date = int(data.get("auth_date", 0))
    if time.time() - auth_date > _TELEGRAM_AUTH_MAX_AGE_SECONDS:
        raise TelegramAuthError("auth payload expired")

    return data


def create_session_cookie(user_id: int) -> str:
    return _serializer.dumps({"user_id": user_id})


def _read_session(session: str | None) -> int | None:
    if not session:
        return None
    try:
        payload = _serializer.loads(session, max_age=SESSION_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    return payload.get("user_id")


def get_current_user(
    session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    user_id = _read_session(session)
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def get_optional_user(
    session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    user_id = _read_session(session)
    return db.get(User, user_id) if user_id else None
