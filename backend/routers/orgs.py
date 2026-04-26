from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import (
    ApiEnvelope,
    AiAutonomousCycleRequest,
    AiDeskControlRequest,
    AiDeskPolicyUpdateRequest,
    AiLiveIntentRequest,
    AiPaperExecutionRequest,
    AiTradePlanRequest,
    BacktestRunRequest,
    OrganizationActivateRequest,
    OrganizationApiTokenCreateRequest,
    OrganizationApiTokenRevokeRequest,
    OrganizationBrandingUpdateRequest,
    OrganizationCreateRequest,
    OrganizationDeliveryActionRequest,
    OrganizationDeliveryUpdateRequest,
    OrganizationFeatureFlagUpdateRequest,
    OrganizationInvitationActionRequest,
    OrganizationMemberInviteRequest,
    OrganizationMemberRemoveRequest,
    OrganizationMemberUpdateRequest,
    OrganizationOnboardingUpdateRequest,
    OrganizationStatusUpdateRequest,
    OrganizationTemplateApplyRequest,
    OrganizationTradeAutomationActionRequest,
    OrganizationTradeAutomationUpdateRequest,
    OrganizationWebhookActionRequest,
    OrganizationWebhookCreateRequest,
    OptionsAutomationCloseRequest,
    OptionsAutomationExecuteRequest,
    OptionsAutomationRefreshRequest,
    OptionsAutomationScanRequest,
    PortfolioTargetExecutionRequest,
    StrategyDeskRunRequest,
    StrategyDeskUpdateRequest,
)
from backend.services.ai_desk_manager_service import (
    build_ai_desk_manager_snapshot,
    build_ai_trade_plan,
    create_ai_live_intent,
    execute_ai_paper_execution,
    get_ai_desk_policy,
    run_ai_desk_control,
    run_or_queue_ai_autonomous_cycle,
    update_ai_desk_policy,
)
from backend.services.options_automation_service import (
    close_options_paper,
    execute_options_paper,
    get_options_automation_snapshot,
    refresh_options_positions,
    run_options_automation_scan,
    sync_options_automation,
)
from backend.services.portfolio_target_execution.service import (
    execute_portfolio_targets,
    get_portfolio_target_execution,
    get_latest_portfolio_target_execution,
    sync_portfolio_target_execution,
)
from backend.services.strategy_engine.service import (
    build_allocator_snapshot,
    get_backtest_run_snapshot,
    get_latest_portfolio_targets,
    get_risk_snapshot,
    get_strategy_desk_metrics,
    get_strategy_desk_snapshot,
    list_strategy_desks,
    run_backtest_for_desk,
    run_strategy_desk,
    update_strategy_desk,
)
from backend.services.trade_automation_service import (
    get_tenant_trade_automation_snapshot,
    run_tenant_trade_automation_action,
    update_tenant_trade_automation_settings,
)
from backend.services.tenant_service import (
    activate_tenant_for_user,
    apply_tenant_onboarding_template,
    create_tenant_api_token,
    create_tenant_member_invitation,
    create_tenant_partner_webhook,
    create_tenant,
    ensure_user,
    get_tenant_analytics_snapshot,
    get_tenant_api_tokens,
    get_tenant_api_usage_snapshot,
    get_tenant_delivery_snapshot,
    get_tenant_feature_flags,
    get_tenant_onboarding_snapshot,
    get_tenant_onboarding_templates_snapshot,
    get_tenant_partner_webhooks,
    get_tenant_security_snapshot,
    get_tenant_support_snapshot,
    list_tenants_for_current_user,
    remove_tenant_membership,
    revoke_tenant_api_token,
    run_tenant_partner_webhook_action,
    run_tenant_invitation_action,
    run_tenant_delivery_action,
    seed_tenant_onboarding_workspace,
    update_tenant_branding,
    update_tenant_delivery_settings,
    update_tenant_feature_flag,
    update_tenant_membership_role,
    update_tenant_onboarding_step,
    update_tenant_status,
)

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.get("", response_model=ApiEnvelope)
def list_organizations(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    user = ensure_user(
        db,
        auth_subject=current_user.auth_subject,
        email=current_user.email,
        name=current_user.name,
        provider=current_user.provider,
        platform_role=current_user.platform_role,
    )
    return envelope(list_tenants_for_current_user(db, user))


@router.post("", response_model=ApiEnvelope)
def create_organization(
    payload: OrganizationCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    user = ensure_user(
        db,
        auth_subject=current_user.auth_subject,
        email=current_user.email,
        name=current_user.name,
        provider=current_user.provider,
        platform_role=current_user.platform_role,
    )
    return envelope(
        create_tenant(
            db,
            owner=user,
            name=payload.name,
            slug=payload.slug,
            plan_key=payload.plan_key,
            billing_email=payload.billing_email,
        )
    )


@router.post("/activate", response_model=ApiEnvelope)
def activate_organization(
    payload: OrganizationActivateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    user = ensure_user(
        db,
        auth_subject=current_user.auth_subject,
        email=current_user.email,
        name=current_user.name,
        provider=current_user.provider,
        platform_role=current_user.platform_role,
    )
    return envelope(activate_tenant_for_user(db, user=user, tenant_slug=payload.tenant_slug))


@router.patch("/branding", response_model=ApiEnvelope)
def update_organization_branding(
    payload: OrganizationBrandingUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_tenant_branding(db, current_user=current_user, updates=payload.model_dump(exclude_unset=True)))


@router.get("/trade-automation", response_model=ApiEnvelope)
def get_organization_trade_automation(
    scope: str | None = Query(default=None),
    scope_key: str | None = Query(default=None),
    linked_account_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        get_tenant_trade_automation_snapshot(
            db,
            current_user=current_user,
            scope=scope,
            scope_key=scope_key,
            linked_account_id=linked_account_id,
        )
    )


@router.get("/strategy-desks", response_model=ApiEnvelope)
def get_organization_strategy_desks(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_strategy_desks(db, current_user=current_user))


@router.get("/strategy-desks/{desk_key}", response_model=ApiEnvelope)
def get_organization_strategy_desk(
    desk_key: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_strategy_desk_snapshot(db, current_user=current_user, desk_key=desk_key))


@router.patch("/strategy-desks/{desk_key}", response_model=ApiEnvelope)
def patch_organization_strategy_desk(
    desk_key: str,
    payload: StrategyDeskUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        update_strategy_desk(
            db,
            current_user=current_user,
            desk_key=desk_key,
            updates=payload.model_dump(exclude_unset=True),
        )
    )


@router.post("/strategy-desks/{desk_key}/runs", response_model=ApiEnvelope)
def post_organization_strategy_desk_run(
    desk_key: str,
    payload: StrategyDeskRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_strategy_desk(db, current_user=current_user, desk_key=desk_key, run_type=payload.run_type))


@router.get("/strategy-desks/{desk_key}/metrics", response_model=ApiEnvelope)
def get_organization_strategy_desk_metrics(
    desk_key: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_strategy_desk_metrics(db, current_user=current_user, desk_key=desk_key))


@router.post("/backtests", response_model=ApiEnvelope)
def post_organization_backtest(
    payload: BacktestRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        run_backtest_for_desk(
            db,
            current_user=current_user,
            desk_key=payload.desk_key,
            request_payload=payload.model_dump(),
        )
    )


@router.get("/backtests/{run_id}", response_model=ApiEnvelope)
def get_organization_backtest(
    run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_backtest_run_snapshot(db, current_user=current_user, run_id=run_id))


@router.get("/allocator", response_model=ApiEnvelope)
def get_organization_allocator(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(build_allocator_snapshot(db, current_user=current_user))


@router.get("/risk", response_model=ApiEnvelope)
def get_organization_risk(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_risk_snapshot(db, current_user=current_user))


@router.get("/ai-desk-manager", response_model=ApiEnvelope)
def get_organization_ai_desk_manager(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(build_ai_desk_manager_snapshot(db, current_user=current_user))


@router.get("/ai-desk-manager/policy", response_model=ApiEnvelope)
def get_organization_ai_desk_policy(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_ai_desk_policy(db, current_user=current_user))


@router.put("/ai-desk-manager/policy", response_model=ApiEnvelope)
def put_organization_ai_desk_policy(
    payload: AiDeskPolicyUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_ai_desk_policy(db, current_user=current_user, request=payload))


@router.post("/ai-desk-manager/control", response_model=ApiEnvelope)
def post_organization_ai_desk_control(
    payload: AiDeskControlRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_ai_desk_control(db, current_user=current_user, request=payload))


@router.post("/ai-desk-manager/autonomous-cycle", response_model=ApiEnvelope)
def post_organization_ai_desk_autonomous_cycle(
    payload: AiAutonomousCycleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_or_queue_ai_autonomous_cycle(db, current_user=current_user, request=payload))


@router.post("/ai-desk-manager/trade-plans", response_model=ApiEnvelope)
def post_organization_ai_trade_plan(
    payload: AiTradePlanRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(build_ai_trade_plan(db, current_user=current_user, request=payload))


@router.post("/ai-desk-manager/paper-executions", response_model=ApiEnvelope)
def post_organization_ai_paper_execution(
    payload: AiPaperExecutionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(execute_ai_paper_execution(db, current_user=current_user, request=payload))


@router.post("/ai-desk-manager/live-intents", response_model=ApiEnvelope)
def post_organization_ai_live_intent(
    payload: AiLiveIntentRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(create_ai_live_intent(db, current_user=current_user, request=payload))


@router.get("/portfolio-targets/latest", response_model=ApiEnvelope)
def get_organization_latest_portfolio_targets(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_latest_portfolio_targets(db, current_user=current_user))


@router.get("/options-automation", response_model=ApiEnvelope)
def get_organization_options_automation(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_options_automation_snapshot(db, current_user=current_user))


@router.post("/options-automation/scan", response_model=ApiEnvelope)
def post_organization_options_automation_scan(
    payload: OptionsAutomationScanRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_options_automation_scan(db, current_user=current_user, request=payload))


@router.post("/options-automation/execute-paper", response_model=ApiEnvelope)
def post_organization_options_automation_execute_paper(
    payload: OptionsAutomationExecuteRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(execute_options_paper(db, current_user=current_user, request=payload))


@router.post("/options-automation/refresh-positions", response_model=ApiEnvelope)
def post_organization_options_automation_refresh_positions(
    payload: OptionsAutomationRefreshRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(refresh_options_positions(db, current_user=current_user, request=payload))


@router.post("/options-automation/close-paper", response_model=ApiEnvelope)
def post_organization_options_automation_close_paper(
    payload: OptionsAutomationCloseRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(close_options_paper(db, current_user=current_user, request=payload))


@router.post("/options-automation/sync", response_model=ApiEnvelope)
def post_organization_options_automation_sync(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(sync_options_automation(db, current_user=current_user))


@router.post("/portfolio-targets/execute", response_model=ApiEnvelope)
def post_organization_execute_portfolio_targets(
    payload: PortfolioTargetExecutionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(execute_portfolio_targets(db, current_user=current_user, request=payload))


@router.get("/portfolio-targets/executions/latest", response_model=ApiEnvelope)
def get_organization_latest_portfolio_target_execution(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_latest_portfolio_target_execution(db, current_user=current_user))


@router.get("/portfolio-targets/executions/{execution_run_id}", response_model=ApiEnvelope)
def get_organization_portfolio_target_execution(
    execution_run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_portfolio_target_execution(db, current_user=current_user, execution_run_id=execution_run_id))


@router.post("/portfolio-targets/executions/{execution_run_id}/sync", response_model=ApiEnvelope)
def post_organization_sync_portfolio_target_execution(
    execution_run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(sync_portfolio_target_execution(db, current_user=current_user, execution_run_id=execution_run_id))


@router.patch("/trade-automation", response_model=ApiEnvelope)
def update_organization_trade_automation(
    payload: OrganizationTradeAutomationUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_tenant_trade_automation_settings(db, current_user=current_user, updates=payload))


@router.post("/trade-automation/actions", response_model=ApiEnvelope)
def run_organization_trade_automation_action(
    payload: OrganizationTradeAutomationActionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_tenant_trade_automation_action(db, current_user=current_user, request=payload))


@router.get("/delivery", response_model=ApiEnvelope)
def get_organization_delivery(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_delivery_snapshot(db, current_user=current_user))


@router.patch("/delivery", response_model=ApiEnvelope)
def update_organization_delivery(
    payload: OrganizationDeliveryUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_tenant_delivery_settings(db, current_user=current_user, updates=payload.model_dump(exclude_unset=True)))


@router.post("/delivery/actions", response_model=ApiEnvelope)
def run_organization_delivery_action(
    payload: OrganizationDeliveryActionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        run_tenant_delivery_action(
            db,
            current_user=current_user,
            action=payload.action,
            provider_id=payload.provider_id,
        )
    )


@router.get("/analytics", response_model=ApiEnvelope)
def get_organization_analytics(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_analytics_snapshot(db, current_user=current_user))


@router.get("/security", response_model=ApiEnvelope)
def get_organization_security(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_security_snapshot(db, current_user=current_user))


@router.post("/members/invite", response_model=ApiEnvelope)
def invite_organization_member(
    payload: OrganizationMemberInviteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        create_tenant_member_invitation(
            db,
            current_user=current_user,
            email=payload.email,
            role=payload.role,
            name=payload.name,
            message=payload.message,
        )
    )


@router.patch("/members", response_model=ApiEnvelope)
def update_organization_member(
    payload: OrganizationMemberUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        update_tenant_membership_role(
            db,
            current_user=current_user,
            membership_id=payload.membership_id,
            role=payload.role,
        )
    )


@router.post("/members/remove", response_model=ApiEnvelope)
def remove_organization_member(
    payload: OrganizationMemberRemoveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(remove_tenant_membership(db, current_user=current_user, membership_id=payload.membership_id))


@router.post("/members/invitations/actions", response_model=ApiEnvelope)
def run_organization_invitation_action(
    payload: OrganizationInvitationActionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        run_tenant_invitation_action(
            db,
            current_user=current_user,
            invitation_id=payload.invitation_id,
            action=payload.action,
        )
    )


@router.get("/tokens", response_model=ApiEnvelope)
def get_organization_api_tokens(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_api_tokens(db, current_user=current_user))


@router.get("/api-usage", response_model=ApiEnvelope)
def get_organization_api_usage(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_api_usage_snapshot(db, current_user=current_user))


@router.post("/tokens", response_model=ApiEnvelope)
def create_organization_api_token(
    payload: OrganizationApiTokenCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        create_tenant_api_token(
            db,
            current_user=current_user,
            name=payload.name,
            scopes=payload.scopes,
            expires_in_days=payload.expires_in_days,
        )
    )


@router.post("/tokens/revoke", response_model=ApiEnvelope)
def revoke_organization_api_token(
    payload: OrganizationApiTokenRevokeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(revoke_tenant_api_token(db, current_user=current_user, token_id=payload.token_id))


@router.get("/webhooks", response_model=ApiEnvelope)
def get_organization_partner_webhooks(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_partner_webhooks(db, current_user=current_user))


@router.post("/webhooks", response_model=ApiEnvelope)
def create_organization_partner_webhook(
    payload: OrganizationWebhookCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        create_tenant_partner_webhook(
            db,
            current_user=current_user,
            name=payload.name,
            url=payload.url,
            events=payload.events,
        )
    )


@router.post("/webhooks/actions", response_model=ApiEnvelope)
def run_organization_partner_webhook_action(
    payload: OrganizationWebhookActionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        run_tenant_partner_webhook_action(
            db,
            current_user=current_user,
            webhook_id=payload.webhook_id,
            action=payload.action,
        )
    )


@router.get("/flags", response_model=ApiEnvelope)
def get_organization_feature_flags(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_feature_flags(db, current_user=current_user))


@router.patch("/flags", response_model=ApiEnvelope)
def update_organization_feature_flag(
    payload: OrganizationFeatureFlagUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    include_enabled = "enabled" in payload.model_fields_set
    include_limit = "limit" in payload.model_fields_set
    return envelope(
        update_tenant_feature_flag(
            db,
            current_user=current_user,
            flag_key=payload.flag_key,
            enabled=payload.enabled if include_enabled else ...,
            limit=payload.limit if include_limit else ...,
            reset=payload.reset,
        )
    )


@router.get("/onboarding", response_model=ApiEnvelope)
def get_organization_onboarding(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_onboarding_snapshot(db, current_user=current_user))


@router.patch("/onboarding", response_model=ApiEnvelope)
def update_organization_onboarding(
    payload: OrganizationOnboardingUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        update_tenant_onboarding_step(
            db,
            current_user=current_user,
            step_key=payload.step_key,
            completed=payload.completed,
        )
    )


@router.get("/onboarding/templates", response_model=ApiEnvelope)
def get_organization_onboarding_templates(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_onboarding_templates_snapshot(db, current_user=current_user))


@router.post("/onboarding/templates/apply", response_model=ApiEnvelope)
def apply_organization_onboarding_template(
    payload: OrganizationTemplateApplyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(apply_tenant_onboarding_template(db, current_user=current_user, template_key=payload.template_key))


@router.post("/onboarding/seed-workspace", response_model=ApiEnvelope)
def seed_organization_onboarding_workspace(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(seed_tenant_onboarding_workspace(db, current_user=current_user))


@router.get("/support", response_model=ApiEnvelope)
def get_organization_support_snapshot(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_tenant_support_snapshot(db, current_user=current_user))


@router.post("/status", response_model=ApiEnvelope)
def update_organization_status(
    payload: OrganizationStatusUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_tenant_status(db, current_user=current_user, status=payload.status))
