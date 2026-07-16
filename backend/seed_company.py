"""Seed a company with the frontend's API key so the profile page works."""
import asyncio
import bcrypt
from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import Company

RAW_KEY = "test-key-for-frontend-slice-4"

async def main():
    async with SessionLocal() as session:
        result = await session.execute(select(Company).where(Company.name == "Default Tenant"))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Company already exists: id={existing.id}, name={existing.name}")
            return

        hashed = bcrypt.hashpw(RAW_KEY.encode(), bcrypt.gensalt()).decode()
        company = Company(name="Default Tenant", api_key_hash=hashed)
        session.add(company)
        await session.commit()
        print(f"Created company: id={company.id}, name={company.name}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
