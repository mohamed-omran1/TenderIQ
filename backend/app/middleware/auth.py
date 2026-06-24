"""Authentication dependency: `Authorization: Bearer <api_key>` -> Company.

Resolves the tenant on every protected request. We never read `company_id`
from the body/query — it's always derived from the key (senior-fullstack skill,
api-security-reviewer API2). Comparison is constant-time via bcrypt.checkpw.

Keys are stored bcrypt-hashed; lookup is by hash. To keep lookups fast while
still constant-time, we hash the incoming key with the same salt-free bcrypt
flow — but bcrypt embeds the salt in the hash, so we can't "look up by hash"
without first hashing with the stored salt. The pragmatic, still-safe approach:
fetch candidate rows whose key could match (a single indexed column is fine for
MVP scale) and constant-time verify. We keep it simple here: linear scan of
companies at MVP tenant counts (<100); revisit with a key-prefix index at scale.
"""
from __future__ import annotations

import bcrypt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company
from app.db.session import get_session
from app.errors import Unauthorized

bearer_scheme = HTTPBearer(auto_error=False)


def _hash_key(raw_api_key: str) -> str:
    """Server-side key hashing for newly created companies."""
    return bcrypt.hashpw(raw_api_key.encode(), bcrypt.gensalt()).decode()


def _verify(raw_api_key: str, hashed: str) -> bool:
    """Constant-time verification (bcrypt.checkpw is constant-time by design)."""
    try:
        return bcrypt.checkpw(raw_api_key.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


async def get_current_company(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Company:
    """Resolve the Bearer token to the owning Company row, or raise 401."""
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise Unauthorized("Missing or invalid API key.")

    raw_key = creds.credentials

    # MVP-scale resolution: scan companies and constant-time verify each hash.
    # A key-prefix index (first 8 chars of the hash) is the scale-out path.
    result = await session.execute(select(Company))
    for company in result.scalars():
        if _verify(raw_key, company.api_key_hash):
            return company

    raise Unauthorized("Missing or invalid API key.")
