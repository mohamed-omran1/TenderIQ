"""REQ-002 Slice 4 — Company profile QA tests.

Every test maps to an Acceptance Criteria item from REQ-002. Tests run against a
real PostgreSQL test database and exercise both the HTTP router and the
LangChain profile_lookup tool.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, CompanyProfile
from app.schemas.company import CompanyProfileSchema

# Valid profile payload reused across tests (REQ-002 QA slice rule).
VALID_PROFILE = {
    "specializations": ["civil", "roads"],
    "financial_capacity": {
        "currency": "EGP",
        "annual_turnover": 50000000.0,
        "available_bonding_capacity": 10000000.0,
    },
    "geographic_reach": ["EG", "SA"],
    "past_projects": [
        {"name": "Cairo Ring Road", "value": 5000000, "year": 2022, "sector": "roads"}
    ],
    "max_project_value": 20000000.0,
}


@pytest.fixture(autouse=True)
async def _clean_profile(clean_profile):
    """Ensure each test in this module starts with no primary-tenant profile."""
    pass


def _auth_headers(raw_api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_api_key}"}


# -----------------------------------------------------------------------------
# GET /company-profile
# -----------------------------------------------------------------------------

class TestGetProfile:
    @pytest.mark.asyncio
    async def test_get_profile_returns_200_when_no_profile_exists(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        resp = await async_client.get(
            "/company-profile", headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert body is not None
        assert body["specializations"] == []
        assert body["financial_capacity"] is None
        assert body["geographic_reach"] == []
        assert body["past_projects"] == []
        assert body["max_project_value"] is None
        assert "company_id" in body
        assert body["company_id"] is not None

    @pytest.mark.asyncio
    async def test_get_profile_returns_200_with_data_when_profile_exists(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        put_resp = await async_client.put(
            "/company-profile", json=VALID_PROFILE, headers=_auth_headers(company_api_key)
        )
        assert put_resp.status_code == 200, put_resp.text

        resp = await async_client.get(
            "/company-profile", headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert body["specializations"] == VALID_PROFILE["specializations"]
        assert body["financial_capacity"] == VALID_PROFILE["financial_capacity"]
        assert body["geographic_reach"] == VALID_PROFILE["geographic_reach"]
        assert body["past_projects"] == VALID_PROFILE["past_projects"]
        assert body["max_project_value"] == VALID_PROFILE["max_project_value"]


# -----------------------------------------------------------------------------
# PUT /company-profile
# -----------------------------------------------------------------------------

class TestPutProfile:
    @pytest.mark.asyncio
    async def test_put_profile_creates_profile_on_first_call(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        resp = await async_client.put(
            "/company-profile", json=VALID_PROFILE, headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 200, resp.text

        put_body = resp.json()
        assert put_body["specializations"] == VALID_PROFILE["specializations"]

        get_resp = await async_client.get(
            "/company-profile", headers=_auth_headers(company_api_key)
        )
        assert get_resp.status_code == 200
        assert get_resp.json() == put_body

    @pytest.mark.asyncio
    async def test_put_profile_updates_existing_profile(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        first = await async_client.put(
            "/company-profile", json=VALID_PROFILE, headers=_auth_headers(company_api_key)
        )
        assert first.status_code == 200, first.text
        first_updated_at = first.json()["updated_at"]

        # Ensure the server-side clock advances enough to produce distinct timestamps.
        await asyncio.sleep(0.05)

        updated = {**VALID_PROFILE, "max_project_value": 99999999.0}
        second = await async_client.put(
            "/company-profile", json=updated, headers=_auth_headers(company_api_key)
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["max_project_value"] == 99999999.0
        assert second_body["updated_at"] > first_updated_at

        get_resp = await async_client.get(
            "/company-profile", headers=_auth_headers(company_api_key)
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["max_project_value"] == 99999999.0

    @pytest.mark.asyncio
    async def test_put_profile_is_atomic_upsert(
        self,
        async_client: AsyncClient,
        company_api_key: str,
        db: AsyncSession,
        company_a: tuple[Company, str],
    ):
        first_profile = {
            **VALID_PROFILE,
            "specializations": ["civil"],
            "max_project_value": 1000000.0,
        }
        await async_client.put(
            "/company-profile", json=first_profile, headers=_auth_headers(company_api_key)
        )

        second_profile = {
            **VALID_PROFILE,
            "specializations": ["roads", "water"],
            "geographic_reach": ["AE"],
            "max_project_value": 50000000.0,
            "past_projects": [
                {"name": "New Project", "value": 1000000, "year": 2024, "sector": "water"}
            ],
        }
        await async_client.put(
            "/company-profile", json=second_profile, headers=_auth_headers(company_api_key)
        )

        result = await db.execute(
            select(CompanyProfile).where(CompanyProfile.company_id == company_a[0].id)
        )
        profile = result.scalar_one()

        # Final state must be exactly the second PUT — no field bleed from the first.
        assert profile.specializations == second_profile["specializations"]
        assert profile.financial_capacity == VALID_PROFILE["financial_capacity"]
        assert profile.geographic_reach == second_profile["geographic_reach"]
        assert profile.past_projects == second_profile["past_projects"]
        assert profile.max_project_value == second_profile["max_project_value"]


# -----------------------------------------------------------------------------
# Validation errors
# -----------------------------------------------------------------------------

class TestValidation:
    @pytest.mark.asyncio
    async def test_put_empty_specializations_returns_422(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        payload = {**VALID_PROFILE, "specializations": []}
        resp = await async_client.put(
            "/company-profile", json=payload, headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 422
        assert "specialisation" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_put_negative_max_project_value_returns_422(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        payload = {**VALID_PROFILE, "max_project_value": -1}
        resp = await async_client.put(
            "/company-profile", json=payload, headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 422
        assert "max_project_value" in resp.text

    @pytest.mark.asyncio
    async def test_put_zero_max_project_value_returns_422(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        payload = {**VALID_PROFILE, "max_project_value": 0}
        resp = await async_client.put(
            "/company-profile", json=payload, headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_put_missing_required_field_returns_422(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        payload = {k: v for k, v in VALID_PROFILE.items() if k != "financial_capacity"}
        resp = await async_client.put(
            "/company-profile", json=payload, headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_put_invalid_currency_format_returns_422(
        self,
        async_client: AsyncClient,
        company_api_key: str,
    ):
        payload = {
            **VALID_PROFILE,
            "financial_capacity": {
                **VALID_PROFILE["financial_capacity"],
                "currency": "INVALID",
            },
        }
        resp = await async_client.put(
            "/company-profile", json=payload, headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 422


# -----------------------------------------------------------------------------
# Security / tenant isolation
# -----------------------------------------------------------------------------

class TestSecurity:
    @pytest.mark.asyncio
    async def test_company_cannot_read_another_companys_profile(
        self,
        async_client: AsyncClient,
        company_api_key: str,
        second_company_api_key: str,
    ):
        await async_client.put(
            "/company-profile", json=VALID_PROFILE, headers=_auth_headers(company_api_key)
        )

        resp = await async_client.get(
            "/company-profile", headers=_auth_headers(second_company_api_key)
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["specializations"] == []
        assert body["financial_capacity"] is None
        assert body["geographic_reach"] == []
        assert body["past_projects"] == []
        assert body["max_project_value"] is None

    @pytest.mark.asyncio
    async def test_company_cannot_overwrite_another_companys_profile(
        self,
        async_client: AsyncClient,
        company_api_key: str,
        second_company_api_key: str,
    ):
        await async_client.put(
            "/company-profile", json=VALID_PROFILE, headers=_auth_headers(company_api_key)
        )

        other_profile = {
            **VALID_PROFILE,
            "specializations": ["mep"],
            "max_project_value": 12345.0,
        }
        second_put = await async_client.put(
            "/company-profile", json=other_profile, headers=_auth_headers(second_company_api_key)
        )
        assert second_put.status_code == 200

        resp = await async_client.get(
            "/company-profile", headers=_auth_headers(company_api_key)
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["specializations"] == VALID_PROFILE["specializations"]
        assert body["max_project_value"] == VALID_PROFILE["max_project_value"]

    @pytest.mark.asyncio
    async def test_financial_capacity_not_in_logs(
        self,
        async_client: AsyncClient,
        company_api_key: str,
        caplog,
    ):
        with caplog.at_level("INFO"):
            resp = await async_client.put(
                "/company-profile", json=VALID_PROFILE, headers=_auth_headers(company_api_key)
            )
        assert resp.status_code == 200

        log_text = "\n".join(caplog.messages)
        assert "annual_turnover" not in log_text
        assert "available_bonding_capacity" not in log_text


# -----------------------------------------------------------------------------
# Agent tool
# -----------------------------------------------------------------------------

class TestAgentTool:
    @pytest.mark.asyncio
    async def test_profile_lookup_tool_returns_pydantic_object(
        self,
        db: AsyncSession,
        company_a: tuple[Company, str],
        profile_lookup_session,
    ):
        from app.agents.tools.profile_lookup import profile_lookup

        company_id = company_a[0].id
        inserted = CompanyProfile(
            company_id=company_id,
            specializations=["civil"],
            financial_capacity={
                "currency": "EGP",
                "annual_turnover": 1000000.0,
                "available_bonding_capacity": 500000.0,
            },
            geographic_reach=["EG"],
            past_projects=[
                {"name": "Direct Insert", "value": 100000, "year": 2023, "sector": "civil"}
            ],
            max_project_value=500000.0,
        )
        db.add(inserted)
        await db.flush()

        result = await profile_lookup.ainvoke({"company_id": company_id})

        assert isinstance(result, CompanyProfileSchema)
        assert result.company_id == company_id
        assert result.specializations == ["civil"]
        assert result.financial_capacity.currency == "EGP"
        assert result.financial_capacity.annual_turnover == 1000000.0
        assert result.financial_capacity.available_bonding_capacity == 500000.0
        assert result.geographic_reach == ["EG"]
        assert len(result.past_projects) == 1
        assert result.past_projects[0].name == "Direct Insert"
        assert result.past_projects[0].value == 100000
        assert result.past_projects[0].year == 2023
        assert result.past_projects[0].sector == "civil"
        assert result.max_project_value == 500000.0

    @pytest.mark.asyncio
    async def test_profile_lookup_tool_raises_value_error_when_no_profile(
        self,
        profile_lookup_session,
    ):
        from app.agents.tools.profile_lookup import profile_lookup

        random_uuid = str(uuid.uuid4())
        with pytest.raises(ValueError) as exc_info:
            await profile_lookup.ainvoke({"company_id": random_uuid})
        assert "No company profile found" in str(exc_info.value)
