"""Aplicação web corporativa."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import shutil
import tempfile
import threading
import time
from datetime import date, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from nfe_consulta import __version__
from nfe_consulta.aging_list import MAX_AGING_UPLOAD
from nfe_consulta.aging_xlsx import convert_aging_xlsx
from nfe_consulta.cancelamento import change_manual_status
from nfe_consulta.banco_config import carregar_caminho_banco, salvar_caminho_banco
from nfe_consulta.config import CNPJ_PADRAO, UF_PADRAO
from nfe_consulta.certificado_arquivo import carregar_certificado_arquivo
from nfe_consulta.certificado_config import (
    carregar_config_certificado_arquivo,
    salvar_config_certificado_arquivo,
)
from nfe_consulta.modelos import NfeConsumoIndevidoErro, NfeLimiteConsultaErro
from nfe_consulta.relatorio_eventos import gravar_eventos_xlsx
from nfe_consulta.servico import (
    ParametrosConsulta,
    executar_consulta,
)
from nfe_consulta.seguranca_banco import criptografado
from nfe_consulta.web.status_view import WebStatus, read_web_status
from nfe_consulta.web.sync_history import read_sync_history
from nfe_consulta.web.admin_history import read_admin_history
from nfe_consulta.web.audit import AuditLog
from nfe_consulta.web.consulta_local import (
    TIPOS_MANIFESTACAO,
    consultar_eventos_exportacao,
    normalizar_filtros,
)
from nfe_consulta.web.consulta_data import (
    banco_indisponivel, banco_nao_sincronizado, estado_consulta, consultar_pagina,
)
from nfe_consulta.web.auth import (
    PUBLIC_USER,
    WebUser,
    admin_session_active,
    clear_admin_cookie,
    set_admin_cookie,
    csrf_token,
    current_user,
    require_admin,
    require_export,
    validate_csrf,
)
from nfe_consulta.web.admin_accounts import AdminAccounts
from nfe_consulta.consulta_numeros import executar_consulta_numeros
from nfe_consulta.web.auto_sync_config import load_schedule, save_schedule
from nfe_consulta.web.scheduler import loop_sincronizacao_automatica
from nfe_consulta.web.settings import WebSettings, get_settings
from nfe_consulta.web.email_alerts import (
    carregar_destinatarios, salvar_destinatarios, smtp_pronto, normalizar_destinatarios,
    contar_pendentes, enviar_pendentes, cancelar_pendentes_removidos, enviar_teste, remetente_atual, salvar_remetente,
)
from nfe_consulta.web.sync_runtime import sincronizar_configurado
from nfe_consulta.web.sync_diagnostics import detalhes_erro, mensagem_erro
from nfe_consulta import janela_consulta
from nfe_consulta.web.uploads import consolidar_txts
from nfe_consulta.web.xml_routes import register_xml_routes
from nfe_consulta.web.xml_store import MAX_UPLOAD, import_history
from nfe_consulta.web.xml_upload_limit import XmlUploadLimit
from nfe_consulta.web.export_queue import ExportQueue, ExportQueueBusy
from nfe_consulta.web.export_jobs import ExportJobs, current_export_job
from nfe_consulta.web.downloads import DownloadArchive, archive_file, cleanup_loop, period_label, report_filename


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _format_date_br(value) -> str:
    if not value:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    text = str(value)
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], pattern).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return text


TEMPLATES.env.filters["date_br"] = _format_date_br


def _context(
    request: Request,
    user: WebUser | None,
    **extra,
) -> dict:
    settings: WebSettings = request.app.state.settings
    contexto = {
        "request": request,
        "user": user,
        "version": __version__,
        "sync_window": janela_consulta.janela_de(request.app.state.scheduler_settings),
        "csrf": csrf_token(user, settings) if user else "",
    }
    contexto.update(extra)
    return contexto


def _render(
    request: Request,
    template: str,
    user: WebUser | None,
    *,
    status_code: int = 200,
    **extra,
):
    extra.setdefault("http_status", status_code)
    return TEMPLATES.TemplateResponse(
        request=request,
        name=template,
        context=_context(request, user, **extra),
        status_code=status_code,
    )



def _database_error_message(
    exc: Exception,
    settings: WebSettings,
    user: WebUser,
) -> str:
    if settings.environment == "development" and user.is_admin:
        return f"{type(exc).__name__}: {exc}"
    return (
        "Banco de manifestações indisponível ou ainda não sincronizado. "
        "Procure o responsável pela aplicação."
    )

def _database_path(settings: WebSettings) -> Path:
    return carregar_caminho_banco(
        settings.database_path,
        path_file=settings.database_path_file,
    )


def _consulta_database_state(settings: WebSettings) -> dict:
    caminho = _database_path(settings)
    if not caminho.is_file():
        return banco_indisponivel()

    from nfe_consulta.seguranca_banco import abrir_banco
    try:
        conn = abrir_banco(caminho, settings.current_database_password(), somente_leitura=True)
        try:
            return estado_consulta(conn, CNPJ_PADRAO)
        finally:
            conn.close()
    except Exception:
        return banco_nao_sincronizado(com_erro=True)


def _database_web(settings: WebSettings) -> dict:
    caminho = _database_path(settings)
    return {
        "path": str(caminho),
        "name": caminho.name,
        "ready": caminho.is_file(),
        "encrypted": criptografado(caminho) if caminho.is_file() else None,
        "configured": bool(
            settings.database_path_file
            and settings.database_path_file.is_file()
        ),
        "password_configured": bool(settings.current_database_password()),
    }


def _status_web(settings: WebSettings) -> WebStatus:
    return read_web_status(
        _database_path(settings),
        CNPJ_PADRAO,
        password=settings.current_database_password(),
    )


def _filtros_iniciais():
    hoje = date.today()
    return normalizar_filtros(
        data_inicial=hoje.replace(day=1).isoformat(),
        data_final=hoje.isoformat(),
    )


@lru_cache(maxsize=16)
def _certificado_arquivo_cached(path, password, signature, interval):
    # Apenas metadados de exibição. Validação e sincronização usam o leitor real.
    return carregar_certificado_arquivo(path, password)


def _certificado_web(settings: WebSettings) -> dict:
    config = carregar_config_certificado_arquivo(
        settings.certificate_path_file,
        settings.certificate_password_file,
    )
    if config is not None:
        try:
            stat = config.path.stat()
            signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
            certificado = _certificado_arquivo_cached(config.path, config.password, signature, int(time.monotonic() // 30))
            return {
                "source": "Arquivo PFX/P12",
                "path": str(config.path),
                "name": config.path.name,
                "subject": certificado.subject,
                "valid_to": certificado.valid_to,
                "ready": True,
                "error": None,
            }
        except Exception as exc:
            return {
                "source": "Arquivo PFX/P12",
                "path": str(config.path),
                "name": config.path.name,
                "subject": None,
                "valid_to": None,
                "ready": False,
                "error": str(exc),
            }

    return {
        "source": settings.certificate_store,
        "path": "",
        "name": "",
        "subject": None,
        "valid_to": None,
        "ready": True,
        "error": None,
    }


def _convert_aging_pdf(payload: bytes) -> BytesIO:
    try:
        from nfe_consulta.aging_pdf import convert_aging_pdf
    except (ImportError, OSError) as exc:
        raise ValueError(
            "A leitura de PDF está indisponível neste ambiente. Use um relatório XLSX compatível do Infor LN."
        ) from exc
    return convert_aging_pdf(payload)


def create_app(settings: WebSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    from nfe_consulta.web.settings import validate_corporate_auth
    validate_corporate_auth(settings)
    app = FastAPI(
        title="AtlasDFE",
        version=__version__,
        root_path=settings.root_path,
        docs_url=None if settings.production else "/docs",
        redoc_url=None,
    )
    app.state.downloads = DownloadArchive(settings.audit_log.parent.parent / "dados" / "downloads")
    app.state.downloads_cleanup_task = None
    app.state.settings = settings
    app.state.scheduler_settings = load_schedule(settings)
    app.state.auto_sync_wake = asyncio.Event()
    app.state.schedule_config_lock = asyncio.Lock()
    app.state.admin_accounts = AdminAccounts(
        settings.admin_accounts_path or settings.database_path.parent / "admin_accounts.db",
        bootstrap_username=settings.admin_username,
        bootstrap_password=settings.admin_password,
    ) if settings.local_auth_enabled else None
    app.state.login_attempts = {}
    app.state.login_lock = threading.Lock()
    app.state.email_test_next_at = 0.0
    app.state.export_queue = ExportQueue()
    app.state.export_jobs = ExportJobs()
    app.state.sync_lock = threading.Lock()
    app.state.audit = AuditLog(settings.audit_log)
    app.state.auto_sync_task = None
    app.state.auto_sync_next_at = None

    @app.on_event("startup")
    async def start_auto_sync():
        app.state.downloads_cleanup_task = asyncio.create_task(cleanup_loop(app.state.downloads))
        app.state.auto_sync_task = asyncio.create_task(
            loop_sincronizacao_automatica(app)
        )
        app.state.audit.write_system(
            "sefaz_scheduler",
            "iniciado",
            horarios=list(app.state.scheduler_settings.auto_sync_times),
            habilitado=app.state.scheduler_settings.auto_sync_enabled,
            recuperacao_cons_nsu=settings.recovery_enabled,
            dias=list(app.state.scheduler_settings.auto_sync_weekdays),
            data_unica=settings.auto_sync_once_date.isoformat() if settings.auto_sync_once_date else None,
            max_lotes=settings.auto_sync_max_lotes,
        )

    @app.on_event("shutdown")
    async def stop_auto_sync():
        await app.state.export_jobs.close()
        cleanup = app.state.downloads_cleanup_task
        if cleanup is not None:
            cleanup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup
        task = app.state.auto_sync_task
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.audit.write_system(
            "sefaz_scheduler",
            "encerrado",
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        with janela_consulta.usar_janela(lambda: app.state.scheduler_settings):
            return await security_headers_inner(request, call_next)

    async def security_headers_inner(request: Request, call_next):
        path = request.url.path
        if not settings.local_auth_enabled and (
            path in {"/login", "/cadastro", "/admin/login", "/admin/logout", "/admin/contas"}
            or path.startswith("/admin/contas/")
        ):
            return JSONResponse({"detail": "Recurso de contas locais desativado."}, status_code=404,
                headers={"Cache-Control": "no-store"})
        if settings.corporate_auth_enabled and path != "/healthz" and not path.startswith("/static/"):
            user = current_user(request)
            if not user.authenticated:
                return JSONResponse({"detail": "Autenticação corporativa necessária."}, status_code=401,
                    headers={"Cache-Control": "no-store"})
            if (path.startswith("/admin") or path.startswith("/atualizar") or
                    path == "/xml/importar" or path.startswith("/xml/lote/")) and not user.is_admin:
                return JSONResponse({"detail": "Permissão administrativa necessária."}, status_code=403,
                    headers={"Cache-Control": "no-store"})
        protected = (path.startswith("/atualizar") or
                     (path.startswith("/admin/") and path not in {"/admin/login", "/admin/logout"}) or
                     path == "/xml/importar" or path.startswith("/xml/lote/"))
        if protected and not current_user(request).is_admin:
            if request.method == "GET":
                return RedirectResponse(request.url_for("admin_login_page"), status_code=303,
                    headers={"Cache-Control": "no-store"})
            return JSONResponse({"detail": "Entre para acessar a área Admin."}, status_code=401,
                headers={"Cache-Control": "no-store"})
        public_path = path in {"/login", "/cadastro", "/admin/login", "/healthz"} or path.startswith("/static/")
        if not public_path and not current_user(request).authenticated:
            if request.method in {"GET", "HEAD"}:
                return RedirectResponse(request.url_for("login_page"), status_code=303,
                    headers={"Cache-Control": "no-store"})
            return JSONResponse({"detail": "Entre para acessar o AtlasDFE."}, status_code=401,
                headers={"Cache-Control": "no-store"})
        if path in {"/aging", "/excel", "/xml/importar", "/admin/login", "/login", "/cadastro", "/admin/contas", "/admin/notas/situacao"} and request.method == "POST":
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Tamanho da requisição inválido."}, status_code=400)
            maximum = MAX_UPLOAD + 2 * 1024 * 1024 if path == "/xml/importar" else settings.max_upload_bytes + 65536 if path == "/excel" else 65536 if path == "/admin/notas/situacao" else 8192
            if path == "/aging":
                maximum = MAX_AGING_UPLOAD + 65536
            if size > maximum:
                return JSONResponse({"detail": "Requisição excede o limite."}, status_code=413)

        export_request = path in {"/excel", "/consulta/exportar", "/xml/exportar"}
        if export_request and request.headers.get("x-nfe-background") == "1" and current_export_job.get() is None:
            if request.method != ("POST" if path == "/excel" else "GET"):
                return JSONResponse({"detail": "Método inválido."}, status_code=405)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > settings.max_upload_bytes + 65536:
                    return JSONResponse({"detail": "Requisição excede o limite."}, status_code=413)
            period = "Não informado"
            if request.method == "GET":
                try:
                    selected = normalizar_filtros(data_inicial=request.query_params.get("data_inicial", ""), data_final=request.query_params.get("data_final", ""))
                    period = period_label(start=selected.data_inicial, end=selected.data_final)
                except (TypeError, ValueError):
                    pass
            try:
                job = app.state.export_jobs.start(app, request.scope, bytes(body), period=period,
                    owner=current_user(request).username)
            except ExportQueueBusy as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                    headers={"Retry-After": "15", "Cache-Control": "no-store"})
            return JSONResponse({"job_id": job['id'], "downloads_url": str(request.url_for("downloads_page"))},
                status_code=202, headers={"Cache-Control": "no-store"})
        if export_request:
            try:
                async with app.state.export_queue.slot():
                    job = current_export_job.get()
                    if job is not None:
                        job['status'] = 'generating'
                    response = await call_next(request)
            except ExportQueueBusy as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                    headers={"Retry-After": "15", "Cache-Control": "no-store"})
        else:
            response = await call_next(request)

        if path != "/admin/logout" and admin_session_active(request):
            set_admin_cookie(response, settings, request.cookies[settings.admin_cookie_name],
                secure=request.url.scheme == "https")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "style-src 'self'; "
            "script-src 'self'; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        if settings.production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if path.startswith("/static/") and response.status_code in {200, 304}:
            version = request.query_params.get("v", "")
            policy = "public, max-age=31536000, immutable" if version.startswith(__version__ + "-") else "public, max-age=0, must-revalidate"
            response.headers.setdefault("Cache-Control", policy)
        elif not path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code in {401, 403}:
            return _render(
                request,
                "error.html",
                current_user(request),
                status_code=exc.status_code,
                title="Acesso negado",
                message=str(exc.detail),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "version": __version__,
        }

    @app.get("/aging", response_class=HTMLResponse)
    async def aging_page(request: Request, user: Annotated[WebUser, Depends(require_export)]):
        return _render(request, "aging.html", user)

    @app.post("/aging")
    async def aging_export(
        request: Request,
        user: Annotated[WebUser, Depends(require_export)],
        csrf: str = Form(...),
        files: UploadFile = File(...),
    ):
        validate_csrf(csrf, user, settings)
        extension = Path(files.filename or "").suffix.lower()
        converter = {".pdf": _convert_aging_pdf, ".xlsx": convert_aging_xlsx}.get(extension)
        if converter is None:
            return _render(request, "aging.html", user, status_code=400,
                           error="Selecione o PDF do Infor LN ou uma planilha .xlsx compatível.")
        payload = await files.read(MAX_AGING_UPLOAD + 1)
        if len(payload) > MAX_AGING_UPLOAD:
            return _render(request, "aging.html", user, status_code=413,
                           error="O relatório excede o limite de 5 MiB.")
        try:
            output = await run_in_threadpool(converter, payload)
        except ValueError as exc:
            report = ("AtlasDFE · Aging List · Relatório de erros\n\n" + str(exc) + "\n").encode("utf-8-sig")
            return _render(
                request, "aging.html", user, status_code=422,
                error="A conversão encontrou erros. Confira o relatório.",
                error_report=base64.b64encode(report).decode("ascii"),
                error_preview=str(exc).splitlines()[:5],
            )
        return Response(
            content=output.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="aging_list.xlsx"',
                     "Cache-Control": "no-store"},
        )

    @app.get("/downloads", response_class=HTMLResponse)
    async def downloads_page(request: Request, user: Annotated[WebUser, Depends(current_user)]):
        try:
            items = await run_in_threadpool(app.state.downloads.list, admin=user.is_admin)
            error = None
        except OSError:
            items = []
            error = "Não foi possível consultar os downloads. Confira a pasta de arquivos no servidor."
        jobs = [job for job in app.state.export_jobs.list() if job['status'] != 'complete']
        return _render(request, "downloads.html", user, items=items, jobs=jobs, error=error)

    @app.get("/downloads/estado")
    async def downloads_state(request: Request, user: Annotated[WebUser, Depends(current_user)], ids: str = ""):
        records = app.state.export_jobs.list()
        selected_ids = set(ids.split(',')[:20])
        tracked = [dict(id=job['id'], status=job['status'], archive_id=job['archive_id'])
                   for job in records if job['id'] in selected_ids]
        found = {job['id'] for job in tracked}
        tracked.extend(dict(id=identifier, status='unavailable', archive_id=None)
                       for identifier in selected_ids - found)
        try:
            items = await run_in_threadpool(app.state.downloads.list, admin=user.is_admin)
        except OSError:
            raise HTTPException(503, "Não foi possível consultar os downloads.")
        html = TEMPLATES.get_template('downloads_table.html').render(request=request, user=user,
            items=items, jobs=[job for job in records if job['status'] != 'complete'],
            csrf=csrf_token(user, settings))
        return JSONResponse(dict(jobs=tracked, html=html), headers={"Cache-Control": "no-store"})

    @app.post("/downloads/tarefas/{identifier}/cancelar", name="cancel_export_job")
    async def cancel_export_job(
        request: Request, identifier: str,
        user: Annotated[WebUser, Depends(current_user)], csrf: str = Form(...),
    ):
        validate_csrf(csrf, user, settings)
        job = app.state.export_jobs.records.get(identifier)
        if job is None:
            raise HTTPException(404, "Solicitação não encontrada.")
        if not user.is_admin and job.get('owner') != user.username:
            raise HTTPException(403, "Você só pode cancelar suas próprias solicitações.")
        if not app.state.export_jobs.cancel(identifier):
            raise HTTPException(409, "A geração já terminou. O arquivo pronto permanece disponível.")
        await run_in_threadpool(app.state.audit.write, request, user, "export_cancel", "ok", job=identifier)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"status": job['status']})
        return RedirectResponse(request.url_for("downloads_page"), status_code=303)

    @app.get("/downloads/{identifier}")
    async def archived_download(request: Request, identifier: str, user: Annotated[WebUser, Depends(current_user)]):
        item = await run_in_threadpool(app.state.downloads.get, identifier, admin=user.is_admin)
        if item is None:
            raise HTTPException(404, "Arquivo indisponível ou expirado. Gere o relatório novamente.")
        await run_in_threadpool(app.state.audit.write, request, user, "download_archived", "ok")
        return FileResponse(app.state.downloads.directory / f"{item['id']}.bin",
            filename=item['filename'], media_type=item['media_type'],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/cadastro", response_class=HTMLResponse, name="register_page")
    async def register_page(request: Request, user: Annotated[WebUser, Depends(current_user)]):
        if user.authenticated:
            return RedirectResponse(request.url_for("consulta_page"), status_code=303)
        return _render(request, "register.html", user)

    @app.post("/cadastro", name="register_submit")
    async def register_submit(
        request: Request, user: Annotated[WebUser, Depends(current_user)],
        csrf: str = Form(...), username: str = Form(...), password: str = Form(...),
        password_confirmation: str = Form(...), email: str = Form(""),
    ):
        validate_csrf(csrf, user, settings)
        if user.authenticated:
            raise HTTPException(403, "Saia da conta atual para criar outro acesso.")
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with app.state.login_lock:
            attempts = app.state.login_attempts
            # Usa o mesmo limite de memória do login, com contagem separada.
            for key in list(attempts):
                attempts[key] = [t for t in attempts[key] if now - t < 300]
                if not attempts[key]:
                    del attempts[key]
            key = ("register", address)
            recent = attempts.get(key, [])
            if len(recent) >= 5 or (len(attempts) >= 5000 and key not in attempts):
                raise HTTPException(429, "Muitos cadastros. Aguarde alguns minutos e tente novamente.")
            attempts[key] = recent + [now]
        try:
            if password != password_confirmation:
                raise ValueError("As senhas não coincidem. Digite novamente.")
            # O perfil é fixo no servidor, independentemente do formulário.
            email = app.state.admin_accounts.normalize_email(email)
            await run_in_threadpool(app.state.admin_accounts.create, username, password, "fiscal", email)
        except ValueError as exc:
            return _render(request, "register.html", user, status_code=400, error=str(exc),
                           username=username[:64], email=email[:254])
        await run_in_threadpool(app.state.audit.write, request, user, "user_register", "ok")
        return _render(request, "login.html", user,
            admin_configured=app.state.admin_accounts.configured(),
            success="Conta criada. Entre com seu e-mail ou usuário e senha.")

    @app.get("/login", response_class=HTMLResponse, name="login_page")
    @app.get("/admin/login", response_class=HTMLResponse)
    async def admin_login_page(
        request: Request,
        user: Annotated[WebUser, Depends(current_user)],
    ):
        if user.authenticated:
            return RedirectResponse(
                url=request.url_for("atualizar_page" if user.is_admin else "consulta_page"),
                status_code=303,
            )

        return _render(
            request,
            "login.html",
            user,
            admin_configured=app.state.admin_accounts.configured(),
        )

    @app.post("/login", name="login_submit")
    @app.post("/admin/login")
    async def admin_login(
        request: Request,
        user: Annotated[WebUser, Depends(current_user)],
        csrf: str = Form(...),
        username: str = Form(...),
        password: str = Form(...),
    ):
        validate_csrf(csrf, user, settings)
        if len(username) > 254 or len(password) > 1024:
            return _render(request, "login.html", user, status_code=400,
                           admin_configured=True, error="Usuário ou senha inválidos.")

        if not app.state.admin_accounts.configured():
            return _render(
                request,
                "login.html",
                user,
                status_code=503,
                admin_configured=False,
                error="Login de administrador ainda não configurado no servidor.",
            )

        address = request.client.host if request.client else "unknown"
        key = ("account", username.strip().casefold())
        ip_key = ("ip", address)
        now = time.monotonic()
        with app.state.login_lock:
            attempts = app.state.login_attempts
            if len(attempts) > 2000:
                for old_key in list(attempts):
                    attempts[old_key] = [t for t in attempts[old_key] if now - t < 300]
                    if not attempts[old_key]:
                        del attempts[old_key]
            recent = [t for t in attempts.get(key, ()) if now - t < 300]
            recent_ip = [t for t in attempts.get(ip_key, ()) if now - t < 300]
            blocked = (len(recent) >= 10 or len(recent_ip) >= 30
                       or (len(attempts) >= 5000 and key not in attempts))
            if not blocked:
                attempts[key] = recent + [now]
                attempts[ip_key] = recent_ip + [now]
        if blocked:
            return _render(request, "login.html", user, status_code=429,
                           admin_configured=True, error="Muitas tentativas. Tente novamente em alguns minutos.")

        logged_username = await run_in_threadpool(app.state.admin_accounts.authenticate, username, password)
        if not logged_username:
            await run_in_threadpool(app.state.audit.write,
                request,
                PUBLIC_USER,
                "admin_login",
                "erro",
                reason="invalid_credentials",
            )
            return _render(
                request,
                "login.html",
                user,
                status_code=401,
                admin_configured=True,
                error="Usuário ou senha inválidos.",
            )

        with app.state.login_lock:
            app.state.login_attempts.pop(key, None)
        admin_user = WebUser(
            username=logged_username,
            display_name=logged_username,
            is_admin=app.state.admin_accounts.role(logged_username) == "admin",
            role=app.state.admin_accounts.role(logged_username) or "consulta",
        )
        await run_in_threadpool(app.state.audit.write,
            request,
            admin_user,
            "admin_login",
            "ok",
        )

        response = RedirectResponse(
            url=request.url_for("atualizar_page" if admin_user.is_admin else "consulta_page"),
            status_code=303,
        )
        set_admin_cookie(response, settings, app.state.admin_accounts.new_session(logged_username),
            secure=request.url.scheme == "https")
        return response

    @app.post("/admin/logout")
    async def admin_logout(
        request: Request,
        user: Annotated[WebUser, Depends(current_user)],
        csrf: str = Form(...),
    ):
        if not user.authenticated:
            raise HTTPException(401, "Entre para encerrar a sessão.")
        validate_csrf(csrf, user, settings)
        app.state.admin_accounts.revoke_session(user.session_token)
        await run_in_threadpool(app.state.audit.write,
            request,
            user,
            "admin_logout",
            "ok",
        )
        response = RedirectResponse(
            url=request.url_for("consulta_page"),
            status_code=303,
        )
        clear_admin_cookie(response, settings, secure=request.url.scheme == "https")
        return response

    @app.post("/admin/agendamento", name="admin_schedule_update")
    async def admin_schedule_update(
        request: Request, user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...), enabled: bool = Form(False),
        times: str = Form(""), weekdays: list[int] = Form([]),
        window_start: str | None = Form(None), window_end: str | None = Form(None),
    ):
        validate_csrf(csrf, user, settings)
        async with app.state.schedule_config_lock:
            previous = app.state.scheduler_settings
            try:
                updated = await run_in_threadpool(save_schedule, previous,
                    enabled=enabled, times=[value.strip() for value in times.split(',')], weekdays=weekdays,
                    window_start=window_start, window_end=window_end)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            except OSError:
                raise HTTPException(503, "Não foi possível salvar o agendamento. A configuração anterior foi preservada.")
            app.state.scheduler_settings = updated
            app.state.auto_sync_next_at = None
            app.state.auto_sync_wake.set()
        await run_in_threadpool(app.state.audit.write, request, user, "auto_sync_config", "ok", enabled=enabled,
                              times=list(updated.auto_sync_times), weekdays=list(updated.auto_sync_weekdays),
                              window_start=updated.sync_window_start, window_end=updated.sync_window_end,
                              previous_window_start=previous.sync_window_start,
                              previous_window_end=previous.sync_window_end)
        return RedirectResponse(str(request.url_for("atualizar_page")) + "?schedule_saved=1", status_code=303)

    @app.get("/admin/contas", response_class=HTMLResponse, name="admin_accounts_page")
    async def admin_accounts_page(
        request: Request, user: Annotated[WebUser, Depends(require_admin)],
    ):
        accounts = await run_in_threadpool(app.state.admin_accounts.list_accounts)
        return _render(request, "admin_accounts.html", user, accounts=accounts,
                       success="Alteração salva." if request.query_params.get("saved") == "1" else None)

    @app.post("/admin/contas", name="admin_accounts_update")
    async def admin_accounts_update(
        request: Request, user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...), action: str = Form(...), username: str = Form(...),
        password: str = Form(""), role: str = Form("fiscal"),
        confirm_delete: bool = Form(False), email: str = Form(""),
    ):
        validate_csrf(csrf, user, settings)
        accounts = app.state.admin_accounts
        try:
            if action == "create":
                email = accounts.normalize_email(email)
                await run_in_threadpool(accounts.create, username, password, role, email)
            elif action == "email":
                await run_in_threadpool(accounts.set_email, username, email)
            elif action == "role":
                await run_in_threadpool(accounts.set_role, username, role)
            elif action in {"activate", "deactivate"}:
                await run_in_threadpool(accounts.set_active, username, action == "activate")
            elif action == "delete":
                if not confirm_delete:
                    raise ValueError("Confirme a exclusão da conta.")
                await run_in_threadpool(accounts.delete, username)
            elif action == "reset":
                await run_in_threadpool(accounts.reset_password, username, password)
            else:
                raise ValueError("Ação inválida.")
        except ValueError as exc:
            return _render(request, "admin_accounts.html", user, status_code=400,
                accounts=await run_in_threadpool(accounts.list_accounts), error=str(exc))
        await run_in_threadpool(app.state.audit.write, request, user, "admin_accounts", "ok",
                              operation=action, target=username.strip())
        return RedirectResponse(str(request.url_for("admin_accounts_page")) + "?saved=1", status_code=303)

    @app.get("/admin/historico", response_class=HTMLResponse)
    async def admin_history_page(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
    ):
        try:
            history = await run_in_threadpool(read_admin_history, settings.audit_log)
            error = None
        except OSError:
            history = ()
            error = "Histórico temporariamente indisponível."
        return _render(request, "admin_history.html", user, history=history, error=error)

    @app.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        user: Annotated[WebUser, Depends(current_user)],
    ):
        return RedirectResponse(
            url=request.url_for("consulta_page"),
            status_code=303,
        )

    @app.get("/consulta", response_class=HTMLResponse)
    async def consulta_page(
        request: Request,
        user: Annotated[WebUser, Depends(current_user)],
        consultar: str = "",
        data_inicial: str = "",
        data_final: str = "",
        numero: str = "",
        serie: str = "",
        chave: str = "",
        codigo: str = "",
        pagina: int = 1,
        situacao: str = "",
    ):
        consultou = consultar == "1" or any(
            valor.strip()
            for valor in (data_inicial, data_final, numero, serie, chave, codigo, situacao)
        )

        if consultou:
            try:
                filtros = normalizar_filtros(
                    data_inicial=data_inicial,
                    data_final=data_final,
                    numero=numero,
                    serie=serie,
                    chave=chave,
                    codigo=codigo,
                    situacao=situacao,
                )
            except ValueError as exc:
                database_state = await run_in_threadpool(_consulta_database_state, settings)
                return _render(
                    request,
                    "consulta.html",
                    user,
                    database_ready=database_state["ready"],
                    database_status_label=database_state["label"],
                    database_notice=database_state["notice"],
                    filtros=_filtros_iniciais(),
                    tipos_manifestacao=TIPOS_MANIFESTACAO,
                    consultou=True,
                    resultado=None,
                    consulta_error=str(exc),
                )
        else:
            filtros = _filtros_iniciais()

        resultado = None
        xml_keys = set()
        erro_consulta = None
        banco = _database_path(settings)
        if consultou:
            leitura = await run_in_threadpool(consultar_pagina, banco, CNPJ_PADRAO, filtros,
                password_provider=settings.current_database_password, pagina=pagina)
            database_state = leitura.database_state
            if not database_state["ready"]:
                erro_consulta = database_state["notice"]
            elif leitura.error is not None:
                erro_consulta = _database_error_message(leitura.error, settings, user)
            else:
                try:
                    resultado = leitura.resultado
                    xml_keys = resultado.xml_keys
                    await run_in_threadpool(app.state.audit.write,
                        request,
                        user,
                        "consulta_eventos",
                        "ok",
                        resultados=resultado.total,
                        pagina=resultado.pagina,
                    )
                except Exception as exc:
                    erro_consulta = _database_error_message(exc, settings, user)
        else:
            database_state = await run_in_threadpool(_consulta_database_state, settings)

        return _render(
            request,
            "consulta.html",
            user,
            database_ready=database_state["ready"],
            database_status_label=database_state["label"],
            database_notice=database_state["notice"],
            filtros=filtros,
            tipos_manifestacao=TIPOS_MANIFESTACAO,
            consultou=consultou,
            resultado=resultado,
            xml_keys=xml_keys,
            consulta_error=erro_consulta,
        )

    @app.get("/consulta/exportar")
    async def exportar_consulta_eventos(
        request: Request,
        user: Annotated[WebUser, Depends(require_export)],
        data_inicial: str = "",
        data_final: str = "",
        numero: str = "",
        serie: str = "",
        chave: str = "",
        codigo: str = "",
        situacao: str = "",
    ):
        banco = _database_path(settings)
        database_state = await run_in_threadpool(_consulta_database_state, settings)
        if not database_state["ready"]:
            raise HTTPException(
                status_code=503,
                detail=database_state["notice"],
            )

        try:
            filtros = normalizar_filtros(
                data_inicial=data_inicial,
                data_final=data_final,
                numero=numero,
                serie=serie,
                chave=chave,
                codigo=codigo,
                situacao=situacao,
            )
            eventos = await run_in_threadpool(
                consultar_eventos_exportacao,
                banco,
                CNPJ_PADRAO,
                filtros,
                password=settings.current_database_password(),
            )
        except (ValueError, OSError, RuntimeError) as exc:
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "export_eventos_excel",
                "erro",
                reason=type(exc).__name__,
            )
            raise HTTPException(
                status_code=400,
                detail=_database_error_message(exc, settings, user),
            ) from exc
        except Exception as exc:
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "export_eventos_excel",
                "erro",
                reason="unexpected",
            )
            raise HTTPException(
                status_code=500,
                detail=_database_error_message(exc, settings, user),
            ) from exc

        temporario = Path(tempfile.mkdtemp(prefix="nfe-eventos-web-"))
        nome = report_filename('manifestacao', (evento.data for evento in eventos),
            start=filtros.data_inicial, end=filtros.data_final)
        saida = temporario / nome

        try:
            await run_in_threadpool(
                gravar_eventos_xlsx,
                saida,
                eventos,
                filtros,
            )
        except Exception as exc:
            shutil.rmtree(temporario, ignore_errors=True)
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "export_eventos_excel",
                "erro",
                reason=type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Não foi possível gerar a planilha da consulta.",
            ) from exc

        await run_in_threadpool(app.state.audit.write,
            request,
            user,
            "export_eventos_excel",
            "ok",
            eventos=len(eventos),
        )
        try:
            await archive_file(request, saida, filename=nome,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                documents=len({evento.chave for evento in eventos}),
                period=period_label((evento.data for evento in eventos),
                    start=filtros.data_inicial, end=filtros.data_final))
        except HTTPException:
            shutil.rmtree(temporario, ignore_errors=True)
            raise
        return FileResponse(
            path=saida,
            filename=nome,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(
                shutil.rmtree,
                temporario,
                ignore_errors=True,
            ),
        )

    @app.post("/excel")
    async def exportar_excel_chaves(
        request: Request,
        user: Annotated[WebUser, Depends(require_export)],
        csrf: str = Form(...),
        files: UploadFile = File(...),
        tipo_arquivo: str = Form("chaves"),
    ):
        validate_csrf(csrf, user, settings)
        if tipo_arquivo not in {"chaves", "numeros"}:
            raise HTTPException(400, "Tipo de consulta por arquivo inválido.")
        audit_action = "excel_numeros" if tipo_arquivo == "numeros" else "excel_chaves"

        if not _database_path(settings).is_file():
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                audit_action,
                "erro",
                reason="database_missing",
            )
            return _render(
                request,
                "consulta.html",
                user,
                status_code=503,
                database_ready=False,
                database_status_label="Banco indisponível",
                database_notice="O banco de manifestações ainda não está disponível para consulta.",
                filtros=_filtros_iniciais(),
                tipos_manifestacao=TIPOS_MANIFESTACAO,
                consultou=False,
                resultado=None,
                error="Banco de manifestações indisponível no servidor.",
            )

        temporario = Path(tempfile.mkdtemp(prefix="nfe-web-"))
        try:
            chaves = temporario / ("NOTAS.txt" if tipo_arquivo == "numeros" else "CHAVES.txt")
            resumo_upload = await consolidar_txts(
                [files],
                chaves,
                max_bytes=settings.max_upload_bytes,
                max_chaves=settings.max_keys,
                tipo=tipo_arquivo,
            )
            saida = temporario / 'relatorio.xlsx'
            if tipo_arquivo == "numeros":
                resultado = await run_in_threadpool(executar_consulta_numeros, chaves,
                    _database_path(settings), saida, CNPJ_PADRAO,
                    senha=settings.current_database_password(), max_numeros=settings.max_keys)
            else:
                resultado = await run_in_threadpool(
                    executar_consulta,
                    ParametrosConsulta(
                        chaves=chaves,
                        banco=_database_path(settings),
                        saida=saida,
                        cnpj=CNPJ_PADRAO,
                        uf=UF_PADRAO,
                        sincronizar_sefaz=False,
                        senha_banco=settings.current_database_password(),
                    ),
                )
            nome_planilha = report_filename('manifestacao',
                (evento.data for nota in resultado.resultados for evento in nota.manifestacoes))
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                audit_action,
                "ok",
                arquivos=resumo_upload.arquivos_txt,
                entradas=resumo_upload.chaves,
                tipo=tipo_arquivo,
                notas=resultado.total,
                com_evento=resultado.com_evento,
                erros=resultado.com_erro,
            )

            await archive_file(request, saida, filename=nome_planilha,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                documents=resultado.total,
                period=period_label(evento.data for nota in resultado.resultados for evento in nota.manifestacoes))
            return FileResponse(
                path=saida,
                filename=nome_planilha,
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
                background=BackgroundTask(
                    shutil.rmtree,
                    temporario,
                    ignore_errors=True,
                ),
            )
        except HTTPException:
            shutil.rmtree(temporario, ignore_errors=True)
            raise
        except (ValueError, OSError, RuntimeError) as exc:
            shutil.rmtree(temporario, ignore_errors=True)
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                audit_action,
                "erro",
                reason=type(exc).__name__,
            )
            database_state = await run_in_threadpool(_consulta_database_state, settings)
            return _render(
                request,
                "consulta.html",
                user,
                status_code=400,
                database_ready=database_state["ready"],
                database_status_label=database_state["label"],
                database_notice=database_state["notice"],
                filtros=_filtros_iniciais(),
                tipos_manifestacao=TIPOS_MANIFESTACAO,
                consultou=False,
                resultado=None,
                error=str(exc),
            )
        except Exception:
            shutil.rmtree(temporario, ignore_errors=True)
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                audit_action,
                "erro",
                reason="unexpected",
            )
            database_state = await run_in_threadpool(_consulta_database_state, settings)
            return _render(
                request,
                "consulta.html",
                user,
                status_code=500,
                database_ready=database_state["ready"],
                database_status_label=database_state["label"],
                database_notice=database_state["notice"],
                filtros=_filtros_iniciais(),
                tipos_manifestacao=TIPOS_MANIFESTACAO,
                consultou=False,
                resultado=None,
                error="Não foi possível gerar a planilha.",
            )

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(
        request: Request,
        user: Annotated[WebUser, Depends(current_user)],
    ):
        try:
            status_web = await run_in_threadpool(_status_web, settings)
            erro = None
        except Exception as exc:
            status_web = WebStatus(False, None, None, None, None, None, None, None)
            erro = _database_error_message(exc, settings, user)

        try:
            history = await run_in_threadpool(read_sync_history, settings.audit_log)
        except OSError:
            history = ()

        return _render(
            request,
            "status.html",
            user,
            status_web=status_web,
            sync_history=history,
            error=erro,
        )

    @app.get("/atualizar", response_class=HTMLResponse)
    async def atualizar_page(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
        ok: str | None = None,
        cert_ok: str | None = None,
        db_ok: str | None = None,
        email_ok: str | None = None,
        email_sent: str | None = None,
        email_test_sent: str | None = None,
        email_test_failed: str | None = None,
        lote: int | None = None,
        situacao_ok: int | None = None,
        situacao_acao: str | None = None,
    ):
        try:
            status_web = await run_in_threadpool(_status_web, settings)
            erro = None
        except Exception as exc:
            status_web = WebStatus(False, None, None, None, None, None, None, None)
            erro = _database_error_message(exc, settings, user)

        try:
            email_recipients = "\n".join(carregar_destinatarios(settings.email_recipients_file))
        except (ValueError, OSError):
            email_recipients = ""
            erro = "Lista de destinatários inválida. Salve novamente os endereços para corrigir."

        try:
            email_sender, email_smtp_user = remetente_atual(settings)
        except (ValueError, OSError):
            email_sender, email_smtp_user = settings.smtp_from, settings.smtp_user
            erro = "Configuração do remetente inválida. Salve novamente os dados dos alertas."

        batch = None
        if lote is not None:
            try:
                _, batch = await run_in_threadpool(import_history, _database_path(settings),
                    password=settings.current_database_password(), batch_id=lote)
            except Exception:
                erro = "Não foi possível consultar o resultado da importação."

        return _render(
            request,
            "sefaz.html",
            user,
            batch=batch,
            status_web=status_web,
            success=(
                f"Marcação manual removida de {situacao_ok} nota(s). Cancelamentos da SEFAZ permanecem válidos." if situacao_ok and situacao_acao == 'remover'
                else f"{situacao_ok} nota(s) marcada(s) como Cancelada." if situacao_ok
                else "Sincronização concluída." if ok == "1"
                else "Certificado validado e configurado." if cert_ok == "1"
                else "Banco validado e configurado." if db_ok == "1"
                else "Destinatários dos alertas salvos." if email_ok == "1"
                else f"Teste: {email_test_sent} envio(s) aceito(s) pelo SMTP; {email_test_failed} falha(s). Confira a caixa de entrada e o spam."
                if email_test_sent and email_test_sent.isdigit() and email_test_failed and email_test_failed.isdigit()
                else f"{email_sent} aviso(s) enviado(s)." if email_sent and email_sent.isdigit()
                else None
            ),
            error=erro,
            certificate_store=settings.certificate_store,
            certificate_fixed=bool(settings.certificate_thumbprint),
            certificate_web=await run_in_threadpool(_certificado_web, settings),
            database_web=_database_web(settings),
            cooldown_minutes=settings.sync_cooldown_minutes,
            email_recipients=email_recipients,
            email_sender=email_sender,
            email_smtp_user=email_smtp_user,
            smtp_ready=smtp_pronto(settings),
            email_pending=await run_in_threadpool(contar_pendentes, settings) if status_web.database_ready else None,
        )

    @app.post("/admin/notas/situacao")
    async def alterar_situacao_notas(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...),
        notas: str = Form(...),
        motivo: str = Form(...),
        acao: str = Form("cancelar"),
    ):
        validate_csrf(csrf, user, settings)
        lock = app.state.sync_lock
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Aguarde a importação ou sincronização em andamento.")
        try:
            count = await run_in_threadpool(
                change_manual_status, _database_path(settings), CNPJ_PADRAO, notas,
                user=user.username, reason=motivo, action=acao,
                password=settings.current_database_password(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            lock.release()
        await run_in_threadpool(app.state.audit.write, request, user, "manual_note_status", "ok", count=count, operation=acao)
        return RedirectResponse(
            request.url_for("xml_import_page").include_query_params(situacao_ok=count, situacao_acao=acao),
            status_code=303,
        )

    @app.post("/atualizar/alertas-email")
    async def atualizar_alertas_email(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...),
        recipients: str = Form(""),
        sender: str | None = Form(None),
        smtp_user: str = Form(""),
    ):
        validate_csrf(csrf, user, settings)
        try:
            # Valida todo o formulário antes de gravar a configuração.
            normalizar_destinatarios(recipients)
            if sender is not None:
                salvar_remetente(settings, sender, smtp_user)
            emails = salvar_destinatarios(settings.email_recipients_file, recipients)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            await run_in_threadpool(cancelar_pendentes_removidos, settings, emails)
        except (OSError, RuntimeError) as exc:
            await run_in_threadpool(app.state.audit.write, request, user, "email_recipients_cleanup", "erro", reason=type(exc).__name__)
        await run_in_threadpool(app.state.audit.write, request, user, "email_recipients", "ok", count=len(emails))
        return RedirectResponse(
            url=request.url_for("atualizar_page").include_query_params(email_ok="1"),
            status_code=303,
        )

    @app.post("/atualizar/alertas-email/testar")
    async def atualizar_testar_email(
        request: Request, user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...),
    ):
        validate_csrf(csrf, user, settings)
        with app.state.login_lock:
            now = time.monotonic()
            if now < app.state.email_test_next_at:
                raise HTTPException(429, "Aguarde um minuto antes de enviar outro teste.")
            app.state.email_test_next_at = now + 60
        try:
            enviados, falhas = await run_in_threadpool(enviar_teste, settings, app.state.audit)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            await run_in_threadpool(app.state.audit.write, request, user, "email_test", "erro", reason=type(exc).__name__)
            raise HTTPException(503, "Não foi possível preparar o teste. Confira a configuração dos alertas.") from exc
        await run_in_threadpool(app.state.audit.write, request, user, "email_test", "ok" if not falhas else "erro",
                              sent=enviados, failed=falhas)
        return RedirectResponse(request.url_for("atualizar_page").include_query_params(
            email_test_sent=str(enviados), email_test_failed=str(falhas)), status_code=303)

    @app.post("/atualizar/alertas-email/enviar-pendentes")
    async def atualizar_enviar_pendentes(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...),
    ):
        validate_csrf(csrf, user, settings)
        if not smtp_pronto(settings):
            raise HTTPException(status_code=400, detail="Configure o SMTP antes de enviar.")
        enviados = await run_in_threadpool(enviar_pendentes, settings, app.state.audit)
        await run_in_threadpool(app.state.audit.write, request, user, "email_retry", "ok", sent=enviados)
        return RedirectResponse(
            url=request.url_for("atualizar_page").include_query_params(email_sent=str(enviados)),
            status_code=303,
        )

    @app.post("/atualizar/banco")
    async def atualizar_banco_config(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...),
        database_path: str = Form(...),
    ):
        validate_csrf(csrf, user, settings)

        caminho = database_path.strip()
        if not caminho:
            return RedirectResponse(
                url=request.url_for("atualizar_page"),
                status_code=303,
            )

        try:
            banco = await run_in_threadpool(
                salvar_caminho_banco,
                caminho,
                settings.current_database_password(),
                path_file=settings.database_path_file,
            )
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "database_config",
                "ok",
                arquivo=banco.name,
            )
            return RedirectResponse(
                url=request.url_for("atualizar_page").include_query_params(db_ok="1"),
                status_code=303,
            )
        except Exception as exc:
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "database_config",
                "erro",
                reason=type(exc).__name__,
            )
            try:
                status_web = await run_in_threadpool(_status_web, settings)
            except Exception:
                status_web = WebStatus(False, None, None, None, None, None, None, None)

            return _render(
                request,
                "sefaz.html",
                user,
                status_code=400,
                status_web=status_web,
                error=str(exc),
                certificate_store=settings.certificate_store,
                certificate_fixed=bool(settings.certificate_thumbprint),
                certificate_web=await run_in_threadpool(_certificado_web, settings),
                database_web=_database_web(settings),
                cooldown_minutes=settings.sync_cooldown_minutes,
            )

    @app.post("/atualizar/certificado")
    async def atualizar_certificado(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...),
        certificate_path: str = Form(...),
        certificate_password: str = Form(""),
    ):
        validate_csrf(csrf, user, settings)

        caminho = certificate_path.strip()
        if not caminho:
            return RedirectResponse(
                url=request.url_for("atualizar_page"),
                status_code=303,
            )

        existente = carregar_config_certificado_arquivo(
            settings.certificate_path_file,
            settings.certificate_password_file,
        )
        senha = certificate_password
        if not senha and existente is not None:
            try:
                mesmo_arquivo = (
                    Path(caminho).expanduser().resolve() == existente.path.resolve()
                )
            except OSError:
                mesmo_arquivo = False
            if mesmo_arquivo:
                senha = existente.password

        try:
            certificado = await run_in_threadpool(
                carregar_certificado_arquivo,
                caminho,
                senha,
            )
            if not certificado.cnpj or certificado.cnpj[:8] != CNPJ_PADRAO[:8]:
                raise ValueError(
                    "O certificado não apresenta CNPJ compatível com a empresa."
                )

            config = await run_in_threadpool(
                salvar_config_certificado_arquivo,
                caminho,
                senha,
                path_file=settings.certificate_path_file,
                password_file=settings.certificate_password_file,
            )

            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "certificate_config",
                "ok",
                arquivo=config.path.name,
            )
            return RedirectResponse(
                url=request.url_for("atualizar_page").include_query_params(cert_ok="1"),
                status_code=303,
            )
        except Exception as exc:
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "certificate_config",
                "erro",
                reason=type(exc).__name__,
            )
            try:
                status_web = await run_in_threadpool(_status_web, settings)
            except Exception:
                status_web = WebStatus(False, None, None, None, None, None, None, None)

            return _render(
                request,
                "sefaz.html",
                user,
                status_code=400,
                status_web=status_web,
                error=str(exc),
                certificate_store=settings.certificate_store,
                certificate_fixed=bool(settings.certificate_thumbprint),
                certificate_web=await run_in_threadpool(_certificado_web, settings),
                database_web=_database_web(settings),
                cooldown_minutes=settings.sync_cooldown_minutes,
            )

    @app.post("/atualizar/sincronizar")
    async def atualizar_sincronizar(
        request: Request,
        user: Annotated[WebUser, Depends(require_admin)],
        csrf: str = Form(...),
        max_lotes: int = Form(50),
        confirmacao: str = Form(""),
    ):
        validate_csrf(csrf, user, settings)

        if confirmacao != "sincronizar":
            await run_in_threadpool(app.state.audit.write, request, user, "sefaz_sync", "ignorado", reason="confirmation_required")
            raise HTTPException(status_code=400, detail="Confirme a sincronização manual antes de continuar.")

        if not 1 <= max_lotes <= 500:
            raise HTTPException(status_code=400, detail="Lotes devem estar entre 1 e 500.")

        status_web = await run_in_threadpool(_status_web, settings)
        if not status_web.database_ready:
            await run_in_threadpool(app.state.audit.write, request, user, "sefaz_sync", "ignorado", reason="database_unavailable")
            return _render(
                request,
                "sefaz.html",
                user,
                status_code=503,
                status_web=status_web,
                error="Configure um banco existente antes de sincronizar.",
                certificate_store=settings.certificate_store,
                certificate_fixed=bool(settings.certificate_thumbprint),
                certificate_web=await run_in_threadpool(_certificado_web, settings),
                database_web=_database_web(settings),
                cooldown_minutes=settings.sync_cooldown_minutes,
            )

        if not status_web.can_sync:
            agora = janela_consulta.agora_brasilia()
            await run_in_threadpool(app.state.audit.write, request, user, "sefaz_sync", "ignorado",
                reason="fora_janela" if janela_consulta.proxima_abertura(agora) > agora else "cooldown",
                nsu_salvo=status_web.ult_nsu,
                proxima_tentativa=status_web.available_at.isoformat() if status_web.available_at else None)
            return _render(
                request,
                "sefaz.html",
                user,
                status_code=429,
                status_web=status_web,
                error=(
                    "Sincronização bloqueada temporariamente. "
                    f"Nova tentativa disponível em {status_web.available_label}."
                ),
                certificate_store=settings.certificate_store,
                certificate_fixed=bool(settings.certificate_thumbprint),
                certificate_web=await run_in_threadpool(_certificado_web, settings),
                database_web=_database_web(settings),
                cooldown_minutes=settings.sync_cooldown_minutes,
            )

        lock: threading.Lock = app.state.sync_lock
        if not lock.acquire(blocking=False):
            await run_in_threadpool(app.state.audit.write, request, user, "sefaz_sync", "ignorado", reason="sync_in_progress")
            return _render(
                request,
                "sefaz.html",
                user,
                status_code=409,
                status_web=await run_in_threadpool(_status_web, settings),
                error="Já existe uma sincronização em andamento.",
                certificate_store=settings.certificate_store,
                certificate_fixed=bool(settings.certificate_thumbprint),
                certificate_web=await run_in_threadpool(_certificado_web, settings),
                database_web=_database_web(settings),
                cooldown_minutes=settings.sync_cooldown_minutes,
            )

        try:
            resumo = await run_in_threadpool(
                sincronizar_configurado,
                settings,
                max_lotes=max_lotes,
                audit=app.state.audit,
            )
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "sefaz_sync",
                "ok",
                lotes=resumo.lotes,
                eventos_novos=resumo.eventos_novos,
                documentos_atuais=getattr(resumo, "documentos_atuais", None),
                manifestacoes_atuais=getattr(resumo, "manifestacoes_atuais", None),
                documentos_recuperados=getattr(resumo, "documentos_recuperados", None),
                manifestacoes_recuperadas=getattr(resumo, "manifestacoes_recuperadas", None),
                ult_nsu=resumo.ult_nsu,
                max_nsu=resumo.max_nsu,
                somente_recuperacao=getattr(resumo, "somente_recuperacao", False),
                completo=resumo.completo,
                cache=resumo.cache,
                cooldown_minutos=settings.sync_cooldown_minutes,
                recuperacao_cons_nsu=settings.recovery_enabled,
            )
            return RedirectResponse(
                url=request.url_for("atualizar_page").include_query_params(ok="1"),
                status_code=303,
            )
        except Exception as exc:
            await run_in_threadpool(app.state.audit.write,
                request,
                user,
                "sefaz_sync",
                "erro",
                reason=type(exc).__name__,
                **detalhes_erro(exc),
            )
            try:
                status_web = await run_in_threadpool(_status_web, settings)
            except Exception:
                status_web = WebStatus(False, None, None, None, None, None, None, None)
            http_status = (
                429
                if isinstance(exc, (NfeConsumoIndevidoErro, NfeLimiteConsultaErro))
                else 400
            )
            return _render(
                request,
                "sefaz.html",
                user,
                status_code=http_status,
                status_web=status_web,
                error=mensagem_erro(exc),
                certificate_store=settings.certificate_store,
                certificate_fixed=bool(settings.certificate_thumbprint),
                certificate_web=await run_in_threadpool(_certificado_web, settings),
                database_web=_database_web(settings),
                cooldown_minutes=settings.sync_cooldown_minutes,
            )
        finally:
            lock.release()

    register_xml_routes(app, settings, _render, _database_path)
    app.add_middleware(XmlUploadLimit, max_upload_bytes=settings.max_upload_bytes)
    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "nfe_consulta.web.app:app",
        host=settings.bind_host,
        port=settings.bind_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        workers=1,
    )


if __name__ == "__main__":
    main()
