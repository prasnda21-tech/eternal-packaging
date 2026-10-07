"""HTTP routes for HR & Payroll.

Payroll PDFs use dedicated, ownership-checked endpoints below. Generic file access must
never expose records whose kind is FILES_KIND ("hr_payroll").
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict

from services import storage as obj_storage
from services.hr_payroll import (
    ALLOWED,
    FILES_KIND,
    LINES,
    PERM_ADJUST,
    PERM_EMP_MANAGE,
    PERM_EMP_VIEW,
    PERM_PAY_APPROVE,
    PERM_PAY_CALC,
    PERM_PAY_FINAL,
    PERM_PAY_REOPEN,
    PERM_PAY_REVIEW,
    PERM_PAY_SETTINGS,
    PERM_PAY_VIEW,
    PERM_PIECE_HOLDING,
    PERM_SLIP_ALL,
    PERM_SLIP_OWN,
    STATUS_APPROVAL,
    STATUS_DRAFT,
    STATUS_FINAL,
    STATUS_REVIEW,
    adjust_line,
    calculate_period,
    can_view_payslip,
    create_component,
    create_employee,
    create_employee_with_optional_account,
    create_eternal_account_for_employee,
    create_period,
    create_piece_period,
    ensure_indexes,
    finalize_from_review,
    prepare_piece_period_from_gaji_operator,
    ensure_kasbon_schedule,
    generate_payslip_for_line,
    get_attendance_for_period,
    get_employee,
    get_line,
    get_period,
    get_settings,
    list_components,
    list_eligible_kasbon,
    list_employees,
    list_kasbon_schedules,
    list_lines,
    list_periods,
    public_line,
    public_period,
    reopen_period,
    save_settings,
    transition_period,
    update_component,
    update_employee,
    upsert_attendance_entry,
)
from services.payroll_payment import (
    enrich_period_with_payment_visibility,
    get_payment_for_period,
)


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_hr_payroll_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    require_any_perm,
    user_permissions,
    Depends,
    create_canonical_user=None,
):
    def _dump(payload: LoosePayload) -> dict:
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        return payload.dict()

    async def _perms(user: dict) -> set:
        return set(await user_permissions(user))

    async def _require_payslip_access(user: dict, line: dict) -> set:
        await require_any_perm(user, PERM_SLIP_OWN, PERM_SLIP_ALL)
        perms = await _perms(user)
        if not can_view_payslip(user, line, perms):
            raise HTTPException(403, "Anda tidak berhak melihat slip gaji ini.")
        return perms

    async def _may_create_users(user: dict) -> bool:
        return "users.manage" in await _perms(user)

    @api_router.get("/hr-payroll/meta")
    async def hr_payroll_meta(user=Depends(current_user)):
        await require_any_perm(
            user, PERM_EMP_VIEW, PERM_EMP_MANAGE, PERM_PAY_VIEW, PERM_PAY_CALC,
            PERM_PAY_REVIEW, PERM_PAY_APPROVE, PERM_PAY_FINAL, PERM_PAY_REOPEN,
            PERM_PAY_SETTINGS, PERM_SLIP_OWN, PERM_SLIP_ALL, PERM_ADJUST,
        )
        db = get_db()
        roles = await db.roles.find({}, {"_id": 0, "role": 1, "label": 1, "status": 1}).sort("label", 1).to_list(500)
        return {
            "permissions": {
                "employee_view": PERM_EMP_VIEW, "employee_manage": PERM_EMP_MANAGE,
                "payroll_view": PERM_PAY_VIEW, "calculate": PERM_PAY_CALC,
                "review": PERM_PAY_REVIEW, "approve": PERM_PAY_APPROVE,
                "finalize": PERM_PAY_FINAL, "reopen": PERM_PAY_REOPEN,
                "settings": PERM_PAY_SETTINGS, "payslip_own": PERM_SLIP_OWN,
                "payslip_all": PERM_SLIP_ALL, "adjustment": PERM_ADJUST,
            },
            "statuses": [STATUS_DRAFT, STATUS_REVIEW, STATUS_APPROVAL, STATUS_FINAL],
            "allowed_transitions": ALLOWED,
            "component_kinds": ["earning", "deduction"],
            "calculation_modes": ["fixed", "percentage", "per_day", "per_hour", "per_minute"],
            "roles": roles,
        }

    @api_router.get("/hr-payroll/employees")
    async def employees(
        include_inactive: bool = True,
        q: Optional[str] = None,
        hr_profile_only: bool = False,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PERM_EMP_VIEW, PERM_EMP_MANAGE, PERM_PAY_VIEW, PERM_PIECE_HOLDING)
        return await list_employees(
            get_db(),
            include_inactive=include_inactive,
            q=q or "",
            hr_profile_only=hr_profile_only,
            page=page,
            page_size=page_size,
        )

    @api_router.post("/hr-payroll/employees")
    async def employee_create(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_EMP_MANAGE)
        body = _dump(payload)
        flag = body.get("create_account")
        wants_account = flag is True or isinstance(flag, dict)
        if wants_account:
            if create_canonical_user is None:
                raise HTTPException(500, "Layanan pembuatan akun Eternal belum dikonfigurasi.")
            row = await create_employee_with_optional_account(
                get_db(),
                body,
                user,
                create_canonical_user=create_canonical_user,
                actor_may_create_users=await _may_create_users(user),
            )
        else:
            clean = {k: v for k, v in body.items() if k not in ("create_account", "account")}
            row = await create_employee(get_db(), clean, user)
        return {"employee": row, "message": "Data karyawan disimpan"}

    @api_router.post("/hr-payroll/employees/{employee_id}/eternal-account")
    async def employee_create_eternal_account(employee_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_EMP_MANAGE)
        if create_canonical_user is None:
            raise HTTPException(500, "Layanan pembuatan akun Eternal belum dikonfigurasi.")
        row = await create_eternal_account_for_employee(
            get_db(),
            employee_id,
            _dump(payload),
            user,
            create_canonical_user=create_canonical_user,
            actor_may_create_users=await _may_create_users(user),
        )
        return {"employee": row, "message": "Akun Eternal dibuat dan ditautkan"}

    @api_router.get("/hr-payroll/employees/{employee_id}")
    async def employee_detail(employee_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_EMP_VIEW, PERM_EMP_MANAGE, PERM_PAY_VIEW)
        return {"employee": await get_employee(get_db(), employee_id)}

    @api_router.put("/hr-payroll/employees/{employee_id}")
    async def employee_update(employee_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_EMP_MANAGE)
        row = await update_employee(get_db(), employee_id, _dump(payload), user)
        return {"employee": row, "message": "Data karyawan diperbarui"}

    @api_router.get("/hr-payroll/components")
    async def components(include_inactive: bool = True, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_SETTINGS, PERM_PAY_VIEW, PERM_PAY_CALC)
        rows = await list_components(get_db(), include_inactive=include_inactive)
        return {"components": rows, "count": len(rows)}

    @api_router.post("/hr-payroll/components")
    async def component_create(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_SETTINGS)
        row = await create_component(get_db(), _dump(payload), user)
        return {"component": row, "message": "Komponen gaji disimpan"}

    @api_router.put("/hr-payroll/components/{component_id}")
    async def component_update(component_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_SETTINGS)
        row = await update_component(get_db(), component_id, _dump(payload), user)
        return {"component": row, "message": "Komponen gaji diperbarui"}

    @api_router.get("/hr-payroll/settings")
    async def settings_get(user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_SETTINGS, PERM_PAY_VIEW, PERM_PAY_CALC)
        return {"settings": await get_settings(get_db())}

    @api_router.put("/hr-payroll/settings")
    async def settings_put(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_SETTINGS)
        row = await save_settings(get_db(), _dump(payload), user)
        return {"settings": row, "message": "Pengaturan payroll diperbarui"}

    @api_router.get("/hr-payroll/periods")
    async def periods(status: Optional[str] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_VIEW, PERM_PAY_CALC, PERM_PAY_REVIEW, PERM_PAY_APPROVE, PERM_PAY_FINAL, PERM_PIECE_HOLDING)
        rows = await list_periods(get_db(), status=(status or "").upper())
        return {"periods": rows, "count": len(rows)}

    @api_router.post("/hr-payroll/periods")
    async def period_create(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_CALC)
        body = _dump(payload)
        row = await create_period(
            get_db(), body.get("year"), body.get("month"), body.get("start_date"), body.get("end_date"), user
        )
        return {"period": row, "message": "Periode payroll dibuat"}

    @api_router.get("/hr-payroll/piece-integration")
    async def piece_integration_get(user=Depends(current_user)):
        await require_any_perm(user, PERM_PIECE_HOLDING, PERM_PAY_VIEW, PERM_PAY_CALC)
        from services.operator_wage_claims import get_integration
        return {"integration": await get_integration(get_db())}

    @api_router.put("/hr-payroll/piece-integration")
    async def piece_integration_put(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PIECE_HOLDING)
        from services.operator_wage_claims import set_integration_start
        row = await set_integration_start(get_db(), _dump(payload).get("start_date"), user)
        return {"integration": row, "message": "Batas mulai integrasi disimpan"}

    @api_router.post("/hr-payroll/piece-periods")
    async def piece_period_create(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PIECE_HOLDING)
        body = _dump(payload)
        row = await create_piece_period(get_db(), body.get("start_date"), body.get("end_date"), user)
        return {"period": row, "message": "Periode upah borongan dibuat"}

    @api_router.post("/hr-payroll/piece-periods/from-gaji-operator")
    async def piece_period_from_gaji_operator(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PIECE_HOLDING)
        body = _dump(payload)
        result = await prepare_piece_period_from_gaji_operator(
            get_db(),
            body.get("start_date") or body.get("date_from") or body.get("from"),
            body.get("end_date") or body.get("date_to") or body.get("to"),
            user,
        )
        warnings = []
        period = result.get("period") or {}
        if period.get("id"):
            from services.payroll_accounting_readiness import evaluate_payroll_accounting_readiness
            try:
                readiness = await evaluate_payroll_accounting_readiness(get_db(), period["id"])
            except Exception:
                readiness = {}
            reasons = set(readiness.get("reasons") or [])
            if "payable_account_missing" in reasons:
                warnings.append("Utang Gaji belum dipetakan.")
            if reasons.intersection({"source_mapping_missing", "invalid_mapping"}) or readiness.get("unmapped_sources"):
                warnings.append("Mapping payroll belum lengkap.")
            result["accounting_ready"] = bool(readiness.get("accounting_ready"))
        result["accounting_warnings"] = warnings
        return result

    @api_router.put("/hr-payroll/periods/{period_id}/piece-inputs")
    async def piece_inputs_put(period_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PIECE_HOLDING)
        from services.operator_wage_claims import save_piece_inputs
        period = await get_period(get_db(), period_id)
        row = await save_piece_inputs(get_db(), period, _dump(payload), user)
        return {"period": row, "message": "Input Holding disimpan"}

    @api_router.get("/hr-payroll/periods/{period_id}")
    async def period_detail(period_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_VIEW, PERM_PAY_CALC, PERM_PAY_REVIEW, PERM_PAY_APPROVE, PERM_PAY_FINAL, PERM_PIECE_HOLDING)
        db = get_db()
        period = await get_period(db, period_id)
        presented = public_period(period) or period
        payment = None
        if str(presented.get("status") or "") == STATUS_FINAL:
            payment = await get_payment_for_period(db, period_id)
        return {"period": enrich_period_with_payment_visibility(presented, payment)}

    @api_router.post("/hr-payroll/periods/{period_id}/calculate")
    async def period_calculate(period_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_CALC)
        result = await calculate_period(get_db(), period_id, user)
        return {**result, "message": "Perhitungan payroll selesai"}

    @api_router.post("/hr-payroll/periods/{period_id}/transition")
    async def period_transition(period_id: str, payload: LoosePayload, user=Depends(current_user)):
        body = _dump(payload)
        target = str(body.get("to_status") or "").upper()
        permission = {
            STATUS_DRAFT: PERM_PAY_REVIEW,
            STATUS_REVIEW: PERM_PAY_REVIEW,
            STATUS_APPROVAL: PERM_PAY_APPROVE,
            STATUS_FINAL: PERM_PAY_FINAL,
        }.get(target)
        if not permission:
            raise HTTPException(400, "Status tujuan tidak valid.")
        await require_any_perm(user, permission)
        if target == STATUS_FINAL:
            await require_any_perm(user, "payroll_accounting.post")
            current = await get_period(get_db(), period_id)
            if str(current.get("status") or "") == STATUS_REVIEW:
                # Happy-path Finalkan from REVIEW also requires approval control.
                await require_any_perm(user, PERM_PAY_APPROVE)
        row = await transition_period(get_db(), period_id, target, body.get("note") or "", user)
        return {"period": row, "message": f"Status periode menjadi {target}"}

    @api_router.post("/hr-payroll/periods/{period_id}/finalize-from-review")
    async def period_finalize_from_review(period_id: str, payload: LoosePayload, user=Depends(current_user)):
        """Combined Finalkan: REVIEW → FINAL with approval + finalize + accounting.post."""
        body = _dump(payload)
        await require_any_perm(user, PERM_PAY_APPROVE)
        await require_any_perm(user, PERM_PAY_FINAL)
        await require_any_perm(user, "payroll_accounting.post")
        row = await finalize_from_review(get_db(), period_id, body.get("note") or "", user)
        return {"period": row, "message": "Status periode menjadi FINAL"}

    @api_router.post("/hr-payroll/periods/{period_id}/reopen")
    async def period_reopen(period_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_REOPEN)
        row = await reopen_period(get_db(), period_id, _dump(payload).get("reason") or "", user)
        return {"period": row, "message": "Revisi payroll baru dibuat"}

    @api_router.get("/hr-payroll/periods/{period_id}/lines")
    async def period_lines(period_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_VIEW, PERM_PAY_CALC, PERM_PAY_REVIEW, PERM_PAY_APPROVE, PERM_PAY_FINAL, PERM_PIECE_HOLDING)
        await get_period(get_db(), period_id)
        rows = await list_lines(get_db(), period_id)
        return {"lines": rows, "count": len(rows)}

    @api_router.get("/hr-payroll/lines/{line_id}")
    async def line_detail(line_id: str, user=Depends(current_user)):
        line = await get_line(get_db(), line_id)
        perms = await _perms(user)
        if PERM_PAY_VIEW not in perms and not can_view_payslip(user, line, perms):
            await require_any_perm(user, PERM_PAY_VIEW, PERM_SLIP_OWN, PERM_SLIP_ALL)
            raise HTTPException(403, "Anda tidak berhak melihat baris payroll ini.")
        return {"line": public_line(line)}

    @api_router.post("/hr-payroll/periods/{period_id}/lines/{line_id}/adjust")
    async def line_adjust(period_id: str, line_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_ADJUST)
        body = _dump(payload)
        row = await adjust_line(
            get_db(),
            period_id,
            line_id,
            body.get("amount"),
            body.get("reason") or "",
            body.get("kind") or "",
            user,
            accounting_treatment=body.get("accounting_treatment"),
            accounting_account_id=body.get("accounting_account_id"),
        )
        return {"line": row, "message": "Penyesuaian payroll disimpan"}

    @api_router.put("/hr-payroll/periods/{period_id}/attendance/{employee_id}")
    async def attendance_put(period_id: str, employee_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_CALC, PERM_EMP_MANAGE)
        period = await get_period(get_db(), period_id)
        if period.get("status") != STATUS_DRAFT:
            raise HTTPException(409, "Kehadiran hanya dapat diubah pada periode DRAFT.")
        row = await upsert_attendance_entry(get_db(), period_id, employee_id, _dump(payload), user)
        return {"attendance": row, "message": "Ringkasan kehadiran disimpan"}

    @api_router.get("/hr-payroll/periods/{period_id}/attendance")
    async def attendance_get(period_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_VIEW, PERM_PAY_CALC, PERM_PIECE_HOLDING)
        await get_period(get_db(), period_id)
        rows = await get_attendance_for_period(get_db(), period_id)
        return {"attendance": rows, "count": len(rows)}

    @api_router.get("/hr-payroll/kasbon-schedules")
    async def kasbon_schedules(
        employee_id: Optional[str] = None,
        active_only: bool = False,
        include_eligible: bool = False,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PERM_PAY_VIEW, PERM_PAY_CALC, PERM_PAY_SETTINGS, PERM_PIECE_HOLDING)
        rows = await list_kasbon_schedules(get_db(), employee_id=employee_id or "", active_only=active_only)
        result = {"schedules": rows, "count": len(rows)}
        if include_eligible and employee_id:
            employee = await get_employee(get_db(), employee_id)
            result["eligible_requests"] = await list_eligible_kasbon(get_db(), employee)
        return result

    @api_router.post("/hr-payroll/kasbon-schedules")
    async def kasbon_schedule_create(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_PAY_CALC, PERM_PAY_SETTINGS)
        body = _dump(payload)
        request_id = str(body.get("request_id") or "").strip()
        request_doc = await get_db().employee_requests.find_one({"id": request_id}, {"_id": 0})
        if not request_doc:
            raise HTTPException(404, "Pengajuan kasbon tidak ditemukan.")
        config = await get_settings(get_db())
        if isinstance(body.get("kasbon"), dict):
            config = {**config, "kasbon": {**config["kasbon"], **body["kasbon"]}}
        row = await ensure_kasbon_schedule(get_db(), request_doc, config)
        return {"schedule": row, "message": "Jadwal kasbon disimpan"}

    @api_router.get("/hr-payroll/periods/{period_id}/lines/{line_id}/payslip")
    async def payslip_download(period_id: str, line_id: str, download: bool = True, user=Depends(current_user)):
        line = await get_line(get_db(), line_id)
        if line.get("period_id") != period_id:
            raise HTTPException(404, "Slip gaji tidak ditemukan pada periode ini.")
        await _require_payslip_access(user, line)
        period = await get_period(get_db(), period_id)
        if period.get("status") != STATUS_FINAL:
            raise HTTPException(409, "Slip gaji belum tersedia karena periode belum FINAL.")
        line = await generate_payslip_for_line(get_db(), line_id)
        payslip = line.get("payslip") or {}
        record = await get_db().files.find_one(
            {"id": payslip.get("file_id"), "kind": FILES_KIND, "line_id": line_id, "is_deleted": {"$ne": True}},
            {"_id": 0},
        )
        path = payslip.get("storage_path") or (record or {}).get("storage_path")
        if not path:
            raise HTTPException(404, "File slip gaji tidak ditemukan.")
        try:
            data, _ = obj_storage.get_object(path)
        except FileNotFoundError:
            raise HTTPException(404, "File slip gaji tidak ditemukan.")
        filename = payslip.get("filename") or f"slip-{line_id}.pdf"
        disposition = "attachment" if download else "inline"
        ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "slip-gaji.pdf"
        return FastAPIResponse(
            content=data,
            media_type="application/pdf",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
            },
        )

    @api_router.get("/hr-payroll/history")
    async def payroll_history(
        employee_id: Optional[str] = None,
        year: Optional[int] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PERM_PAY_VIEW, PERM_SLIP_ALL, PERM_SLIP_OWN)
        perms = await _perms(user)
        period_filter = {"status": STATUS_FINAL}
        if year:
            period_filter["year"] = int(year)
        periods = await get_db().hr_payroll_periods.find(period_filter, {"_id": 0}).sort(
            [("year", -1), ("month", -1), ("revision", -1)]
        ).to_list(1000)
        period_ids = [row["id"] for row in periods]
        line_filter = {"period_id": {"$in": period_ids}, "status": STATUS_FINAL}
        if employee_id:
            line_filter["employee_id"] = employee_id
        lines = await get_db()[LINES].find(line_filter, {"_id": 0}).sort("finalized_at", -1).to_list(5000)
        if PERM_PAY_VIEW not in perms and PERM_SLIP_ALL not in perms:
            lines = [row for row in lines if can_view_payslip(user, row, perms)]
            visible_periods = {row["period_id"] for row in lines}
            periods = [row for row in periods if row["id"] in visible_periods]
        return {"periods": periods, "lines": [public_line(row) for row in lines], "count": len(lines)}

    return ensure_indexes
