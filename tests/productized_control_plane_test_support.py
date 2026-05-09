from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import Base, get_db
from backend.models.saas import SubscriptionRecord, Tenant, TenantMembership, User
from backend.services.permissions import resolve_user_permissions


@dataclass
class ProductizedTestContext:
    engine: object
    SessionLocal: sessionmaker
    db: Session
    tenant: Tenant
    user: User
    current_user: CurrentUser

    def close(self) -> None:
        self.db.close()
        self.engine.dispose()


def build_test_context(*, slug: str = "productized-test", plan_key: str = "professional") -> ProductizedTestContext:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    db = SessionLocal()
    tenant = Tenant(slug=slug, name="Productized Test", status="active", plan_key=plan_key)
    user = User(auth_subject=f"{slug}-user", email=f"{slug}@example.test", name="Productized User", platform_role="admin")
    db.add_all([tenant, user])
    db.flush()
    db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner", status="active", is_default=True))
    db.add(SubscriptionRecord(tenant_id=tenant.id, provider="internal-demo", status="active", plan_key=plan_key))
    db.commit()
    db.refresh(tenant)
    db.refresh(user)
    permissions = resolve_user_permissions(membership_role="owner", platform_role="admin", mode="demo")
    current_user = CurrentUser(
        user_id=user.auth_subject,
        auth_subject=user.auth_subject,
        email=user.email,
        name=user.name,
        role="owner",
        platform_role="admin",
        provider="local-demo",
        environment="test",
        mode="demo",
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        tenant_status=tenant.status,
        tenant_plan=tenant.plan_key,
        permissions=permissions,
        authenticated=True,
    )
    return ProductizedTestContext(engine=engine, SessionLocal=SessionLocal, db=db, tenant=tenant, user=user, current_user=current_user)


def build_test_client(context: ProductizedTestContext) -> TestClient:
    from backend.api import app

    def override_db() -> Iterator[Session]:
        db = context.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: context.current_user
    return TestClient(app)


def clear_test_overrides() -> None:
    from backend.api import app

    app.dependency_overrides.clear()
