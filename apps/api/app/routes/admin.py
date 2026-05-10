from __future__ import annotations

import hashlib
import hmac
import random
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import AppUser, EmailVerificationCode, FeatureFlag, PaymentOrder, SubscriptionPlan, Tenant
from app.schemas import (
    AdminLoginOut,
    AdminLoginRequest,
    AdminOverviewOut,
    AppUserPatch,
    EmailCodeOut,
    EmailCodeRequest,
    FeatureFlagPatch,
    InternalBetaLoginRequest,
    PaymentOrderCreate,
    PaymentOrderOut,
    RegisterRequest,
    SubscriptionPlanOut,
    SubscriptionPlanPatch,
)

router = APIRouter(prefix="/admin", tags=["admin"])

PLAN_DEFAULTS = [
    {
        "id": "personal",
        "name": "个人版",
        "tenant_type": "personal",
        "description": "适合个人工程师试用和小型 BOM 处理。",
        "price_label": "¥99 / 年",
        "price_cents": 9900,
        "currency": "CNY",
        "duration_days": 365,
        "seat_limit": 1,
        "bom_limit": 20,
        "sort_order": 10,
    },
    {
        "id": "team",
        "name": "团队版",
        "tenant_type": "team",
        "description": "适合研发小组协同编制 BOM，支持任务协作和历史追溯。",
        "price_label": "¥999 / 年",
        "price_cents": 99900,
        "currency": "CNY",
        "duration_days": 365,
        "seat_limit": 10,
        "bom_limit": 300,
        "sort_order": 20,
    },
    {
        "id": "enterprise",
        "name": "企业版",
        "tenant_type": "enterprise",
        "description": "适合私有化或云端企业级生产使用，开放更完整的数据治理能力。",
        "price_label": "¥9999 / 年",
        "price_cents": 999900,
        "currency": "CNY",
        "duration_days": 365,
        "seat_limit": None,
        "bom_limit": None,
        "sort_order": 30,
    },
]

FEATURE_DEFAULTS = [
    {
        "feature_key": "company_parts",
        "feature_name": "自有物料管理",
        "description": "查询、导入、编辑公司标准物料库。",
        "enabled_for": {"personal", "team", "enterprise"},
    },
    {
        "feature_key": "bom_task_assignment",
        "feature_name": "BOM 编制任务指派",
        "description": "把 BOM 编制、校对、映射确认等工作分配给成员。",
        "enabled_for": {"team", "enterprise"},
    },
    {
        "feature_key": "edit_history",
        "feature_name": "编辑历史查看",
        "description": "查看 BOM 节点字段变更、操作者和撤销记录。",
        "enabled_for": {"team", "enterprise"},
    },
    {
        "feature_key": "node_style_editor",
        "feature_name": "节点样式编辑",
        "description": "通过智能体或图谱交互配置节点颜色、形状和标记。",
        "enabled_for": {"enterprise"},
    },
    {
        "feature_key": "material_mapping",
        "feature_name": "物料映射",
        "description": "自动扫描 BOM 节点并映射到公司标准物料。",
        "enabled_for": {"personal", "team", "enterprise"},
    },
]

TOKEN_PREFIX = "pebs-admin"
PASSWORD_SALT = "pebs-bom-dev-admin"


def _hash_password(password: str) -> str:
    return hashlib.sha256(f"{PASSWORD_SALT}:{password}".encode("utf-8")).hexdigest()


def _hash_code(email: str, code: str, purpose: str) -> str:
    raw = f"{PASSWORD_SALT}:{email.lower()}:{purpose}:{code}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _token_for(username: str, password_hash: str) -> str:
    digest = hashlib.sha256(f"{TOKEN_PREFIX}:{username}:{password_hash}".encode()).hexdigest()
    return f"{TOKEN_PREFIX}.{username}.{digest}"


def _verify_token(token: str | None, user: AppUser) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token, _token_for(user.username, user.password_hash))


def _beta_password_hash(email: str, invite_code: str) -> str:
    return _hash_password(f"internal:{email.lower()}:{invite_code}")


async def _verify_internal_invite(email: str, invite_code: str) -> None:
    if not settings.internal_beta_verify_url:
        return
    payload = {
        "action": "loginWithInvite",
        "productKey": "bom-copilot",
        "email": email,
        "inviteCode": invite_code,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            res = await client.post(settings.internal_beta_verify_url, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="邀请码验证服务暂时不可用") from exc
    if res.status_code >= 400:
        raise HTTPException(status_code=401, detail="邮箱或邀请码验证失败")
    try:
        data = res.json()
    except ValueError:
        data = {}
    message = data.get("message") or "邮箱或邀请码验证失败"
    if message in {"缺少 action", "未知 action"}:
        raise HTTPException(
            status_code=403,
            detail={
                "message": message,
                "action": "apply_invite",
                "status": "inactive",
            },
        )
    ok = data.get("ok", data.get("success", data.get("valid", True)))
    code = data.get("code")
    invite_status = str(data.get("status") or "active").lower()
    if invite_status != "active":
        raise HTTPException(
            status_code=403,
            detail={
                "message": message or "邀请码尚未激活，请先申请邀请码",
                "action": "apply_invite",
                "status": invite_status,
            },
        )
    success_codes = {0, 200, "0", "200", "OK", "ok", "SUCCESS", "success"}
    if ok is False or (code is not None and code not in success_codes):
        raise HTTPException(status_code=401, detail=message)


def _assert_beta_user_active(user: AppUser) -> None:
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    if user.trial_expires_at and user.trial_expires_at < datetime.utcnow():
        raise HTTPException(status_code=403, detail="内测有效期已结束，请联系管理员")


def _plan_price_label(plan: SubscriptionPlan) -> str:
    amount = plan.price_cents / 100
    currency = "¥" if plan.currency == "CNY" else f"{plan.currency} "
    period = f"{plan.duration_days} 天" if plan.duration_days != 365 else "年"
    return f"{currency}{amount:g} / {period}"


async def ensure_admin_defaults(db: AsyncSession) -> None:
    changed = False
    for data in PLAN_DEFAULTS:
        plan = await db.get(SubscriptionPlan, data["id"])
        if not plan:
            db.add(SubscriptionPlan(**data))
            changed = True
        else:
            for field in ("price_cents", "currency", "duration_days"):
                if getattr(plan, field, None) in (None, ""):
                    setattr(plan, field, data[field])
                    changed = True
            if not plan.price_label:
                plan.price_label = _plan_price_label(plan)
                changed = True

    for plan_data in PLAN_DEFAULTS:
        plan_id = plan_data["id"]
        for feature in FEATURE_DEFAULTS:
            existing = (
                await db.execute(
                    select(FeatureFlag).where(
                        FeatureFlag.plan_id == plan_id,
                        FeatureFlag.feature_key == feature["feature_key"],
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue
            db.add(
                FeatureFlag(
                    plan_id=plan_id,
                    feature_key=feature["feature_key"],
                    feature_name=feature["feature_name"],
                    description=feature["description"],
                    enabled=plan_id in feature["enabled_for"],
                    config={},
                )
            )
            changed = True

    default_tenant = await db.get(Tenant, settings.default_tenant_id)
    if not default_tenant:
        default_plan = "enterprise" if settings.deployment_mode == "private" else "personal"
        db.add(
            Tenant(
                id=settings.default_tenant_id,
                name="默认租户",
                tenant_type="enterprise" if settings.deployment_mode == "private" else "personal",
                subscription_plan_id=default_plan,
                status="active",
                owner_name="admin",
            )
        )
        changed = True

    admin_user = (
        await db.execute(select(AppUser).where(AppUser.username == "admin"))
    ).scalar_one_or_none()
    if not admin_user:
        db.add(
            AppUser(
                tenant_id=settings.default_tenant_id,
                username="admin",
                display_name="超级管理员",
                role="super_admin",
                password_hash=_hash_password("admin123456"),
                status="active",
            )
        )
        changed = True

    if changed:
        await db.commit()


async def _overview(db: AsyncSession) -> AdminOverviewOut:
    await ensure_admin_defaults(db)
    plans = (
        await db.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order))
    ).scalars().all()
    tenants = (await db.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    features = (
        await db.execute(
            select(FeatureFlag).order_by(FeatureFlag.plan_id, FeatureFlag.feature_key)
        )
    ).scalars().all()
    users = (await db.execute(select(AppUser).order_by(AppUser.created_at))).scalars().all()
    return AdminOverviewOut(plans=plans, tenants=tenants, features=features, users=users)


async def require_super_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> AppUser:
    await ensure_admin_defaults(db)
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    username = token.split(".")[1] if token.startswith(f"{TOKEN_PREFIX}.") and "." in token else ""
    user = (
        await db.execute(select(AppUser).where(AppUser.username == username))
    ).scalar_one_or_none()
    if not user or user.role != "super_admin" or user.status != "active":
        raise HTTPException(status_code=401, detail="请先使用超级管理员登录")
    if not _verify_token(token, user):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


async def require_active_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> AppUser:
    await ensure_admin_defaults(db)
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    username = token.split(".")[1] if token.startswith(f"{TOKEN_PREFIX}.") and "." in token else ""
    user = (
        await db.execute(select(AppUser).where(AppUser.username == username))
    ).scalar_one_or_none()
    if not user or not _verify_token(token, user):
        raise HTTPException(status_code=401, detail="请先登录内测账号")
    _assert_beta_user_active(user)
    return user


async def get_user_from_token(token: str, db: AsyncSession) -> AppUser:
    username = token.split(".")[1] if token.startswith(f"{TOKEN_PREFIX}.") and "." in token else ""
    user = (
        await db.execute(select(AppUser).where(AppUser.username == username))
    ).scalar_one_or_none()
    if not user or not _verify_token(token, user):
        raise HTTPException(status_code=401, detail="请先登录内测账号")
    _assert_beta_user_active(user)
    return user


@router.get("/overview", response_model=AdminOverviewOut)
async def get_overview(db: AsyncSession = Depends(get_db)) -> AdminOverviewOut:
    return await _overview(db)


@router.get("/plans", response_model=list[SubscriptionPlanOut])
async def public_plans(db: AsyncSession = Depends(get_db)):
    await ensure_admin_defaults(db)
    plans = (
        await db.execute(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.enabled == True)  # noqa: E712
            .order_by(SubscriptionPlan.sort_order)
        )
    ).scalars().all()
    return plans


@router.post("/email-codes", response_model=EmailCodeOut)
async def send_email_code(
    body: EmailCodeRequest,
    db: AsyncSession = Depends(get_db),
) -> EmailCodeOut:
    email = body.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="请输入有效邮箱")
    code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.add(
        EmailVerificationCode(
            email=email,
            purpose=body.purpose,
            code_hash=_hash_code(email, code, body.purpose),
            expires_at=expires_at,
            status="pending",
        )
    )
    await db.commit()
    return EmailCodeOut(
        message="验证码已生成。开发模式下直接显示验证码，接入邮件服务后将发送到邮箱。",
        dev_code=code,
    )


@router.post("/payment-orders", response_model=PaymentOrderOut, status_code=201)
async def create_payment_order(
    body: PaymentOrderCreate,
    db: AsyncSession = Depends(get_db),
) -> PaymentOrderOut:
    await ensure_admin_defaults(db)
    plan = await db.get(SubscriptionPlan, body.plan_id)
    if not plan or not plan.enabled:
        raise HTTPException(status_code=400, detail="订阅方案不可用")
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="请先填写有效邮箱")
    order = PaymentOrder(
        plan_id=plan.id,
        email=email,
        amount_cents=plan.price_cents,
        currency=plan.currency,
        duration_days=plan.duration_days,
        provider="mock",
        status="pending",
        checkout_url=f"/login?plan={plan.id}&order=mock",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


@router.post("/payment-orders/{order_id}/confirm", response_model=PaymentOrderOut)
async def confirm_payment_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
) -> PaymentOrderOut:
    order = await db.get(PaymentOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="支付订单不存在")
    order.status = "paid"
    order.paid_at = datetime.utcnow()
    order.provider_ref = f"mock_{order.id}"
    await db.commit()
    await db.refresh(order)
    return order


@router.post("/login", response_model=AdminLoginOut)
async def login(
    body: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminLoginOut:
    await ensure_admin_defaults(db)
    user = (
        await db.execute(select(AppUser).where(AppUser.username == body.username.strip()))
    ).scalar_one_or_none()
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not hmac.compare_digest(user.password_hash, _hash_password(body.password)):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return AdminLoginOut(token=_token_for(user.username, user.password_hash), user=user)


@router.post("/internal-login", response_model=AdminLoginOut)
async def internal_beta_login(
    body: InternalBetaLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminLoginOut:
    await ensure_admin_defaults(db)
    email = body.email.strip().lower()
    invite_code = body.invite_code.strip()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="请输入有效邮箱")
    if not invite_code:
        raise HTTPException(status_code=400, detail="请输入邀请码")
    await _verify_internal_invite(email, invite_code)

    tenant_id = f"beta_{hashlib.sha1(email.encode('utf-8')).hexdigest()[:16]}"
    username = f"beta_{hashlib.sha1(email.encode('utf-8')).hexdigest()[:20]}"
    display_name = email.split("@", 1)[0] or "内测用户"
    expires_at = datetime.utcnow() + timedelta(days=settings.internal_beta_duration_days)
    password_hash = _beta_password_hash(email, invite_code)

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        tenant = Tenant(
            id=tenant_id,
            name=f"{display_name}的内测企业空间",
            tenant_type="enterprise",
            subscription_plan_id="enterprise",
            status="active",
            owner_name=display_name,
        )
        db.add(tenant)
    else:
        tenant.tenant_type = "enterprise"
        tenant.subscription_plan_id = "enterprise"
        tenant.status = "active"
        tenant.owner_name = tenant.owner_name or display_name

    user = (
        await db.execute(select(AppUser).where(AppUser.username == username))
    ).scalar_one_or_none()
    if not user:
        user = AppUser(
            tenant_id=tenant_id,
            username=username,
            display_name=display_name,
            email=email,
            role="owner",
            password_hash=password_hash,
            status="active",
            trial_expires_at=expires_at,
            bom_import_limit=settings.internal_beta_bom_import_limit,
            bom_export_limit=settings.internal_beta_bom_export_limit,
            bom_import_count=0,
            bom_export_count=0,
        )
        db.add(user)
    else:
        user.tenant_id = tenant_id
        user.display_name = user.display_name or display_name
        user.email = email
        user.role = "owner"
        user.password_hash = password_hash
        user.status = "active"
        user.trial_expires_at = user.trial_expires_at or expires_at
        user.bom_import_limit = settings.internal_beta_bom_import_limit
        user.bom_export_limit = settings.internal_beta_bom_export_limit
    await db.commit()
    await db.refresh(user)
    return AdminLoginOut(token=_token_for(user.username, user.password_hash), user=user)


@router.post("/register", response_model=AdminLoginOut, status_code=201)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminLoginOut:
    await ensure_admin_defaults(db)
    plan = await db.get(SubscriptionPlan, body.plan_id)
    if not plan or not plan.enabled:
        raise HTTPException(status_code=400, detail="订阅方案不可用")
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    email = (body.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="请填写有效邮箱")
    code = body.email_code.strip()
    now = datetime.utcnow()
    verification = (
        await db.execute(
            select(EmailVerificationCode)
            .where(
                EmailVerificationCode.email == email,
                EmailVerificationCode.purpose == "register",
                EmailVerificationCode.status == "pending",
                EmailVerificationCode.expires_at > now,
            )
            .order_by(EmailVerificationCode.created_at.desc())
        )
    ).scalar_one_or_none()
    if not verification or not hmac.compare_digest(
        verification.code_hash,
        _hash_code(email, code, "register"),
    ):
        raise HTTPException(status_code=400, detail="邮箱验证码错误或已过期")
    order = await db.get(PaymentOrder, body.payment_order_id)
    if (
        not order
        or order.plan_id != plan.id
        or order.email != email
        or order.status != "paid"
    ):
        raise HTTPException(status_code=400, detail="订阅订单未支付")
    existing = (
        await db.execute(select(AppUser).where(AppUser.username == username))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="用户名已存在")

    tenant = Tenant(
        id=f"{plan.tenant_type}_{username}",
        name=f"{body.display_name or username}的{plan.name}",
        tenant_type=plan.tenant_type,
        subscription_plan_id=plan.id,
        status="active",
        owner_name=body.display_name or username,
    )
    user = AppUser(
        tenant_id=tenant.id,
        username=username,
        display_name=(body.display_name or username).strip(),
        email=email,
        role="owner",
        password_hash=_hash_password(body.password),
        status="active",
    )
    db.add(tenant)
    db.add(user)
    verification.status = "used"
    verification.verified_at = now
    await db.commit()
    await db.refresh(user)
    return AdminLoginOut(token=_token_for(user.username, user.password_hash), user=user)


@router.patch("/plans/{plan_id}", response_model=AdminOverviewOut)
async def update_plan(
    plan_id: str,
    body: SubscriptionPlanPatch,
    _admin: AppUser = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewOut:
    await ensure_admin_defaults(db)
    plan = await db.get(SubscriptionPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="订阅方案不存在")
    for field in (
        "name",
        "description",
        "price_label",
        "price_cents",
        "currency",
        "duration_days",
        "seat_limit",
        "bom_limit",
        "enabled",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(plan, field, value)
    if not plan.price_label:
        plan.price_label = _plan_price_label(plan)
    await db.commit()
    return await _overview(db)


@router.patch("/features/{feature_id}", response_model=AdminOverviewOut)
async def update_feature(
    feature_id: str,
    body: FeatureFlagPatch,
    _admin: AppUser = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewOut:
    await ensure_admin_defaults(db)
    feature = await db.get(FeatureFlag, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="功能开关不存在")
    if body.enabled is not None:
        feature.enabled = body.enabled
    if body.feature_name is not None:
        feature.feature_name = body.feature_name.strip() or feature.feature_name
    if body.description is not None:
        feature.description = body.description.strip() or None
    if body.config is not None:
        feature.config = body.config
    await db.commit()
    return await _overview(db)


@router.patch("/users/{user_id}", response_model=AdminOverviewOut)
async def update_user(
    user_id: str,
    body: AppUserPatch,
    _admin: AppUser = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewOut:
    await ensure_admin_defaults(db)
    user = await db.get(AppUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.display_name
    if body.email is not None:
        user.email = body.email.strip() or None
    if body.phone is not None:
        user.phone = body.phone.strip() or None
    if body.role is not None:
        user.role = body.role.strip() or user.role
    if body.status is not None:
        user.status = body.status.strip() or user.status
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(status_code=400, detail="密码至少 6 位")
        user.password_hash = _hash_password(body.password)
    await db.commit()
    return await _overview(db)
