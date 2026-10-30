"""Rotas do leitor XML integradas às sessões e ao banco da aplicação."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as MultipartUploadFile

from nfe_consulta.config import CNPJ_PADRAO
from nfe_consulta.web.auth import WebUser, current_user, require_admin, require_export, require_user, validate_csrf
from nfe_consulta.web.consulta_local import normalizar_filtros, consultar_eventos, ResultadoEventos, TIPOS_MANIFESTACAO
from nfe_consulta.web import xml_store as store
from nfe_consulta.web.downloads import archive_file, period_label
from nfe_consulta.cte import linked_ctes
from nfe_consulta.seguranca_banco import abrir_banco


def register_xml_routes(app, settings, render, database_path):
    def filters(inicio, fim):
        return normalizar_filtros(data_inicial=inicio, data_final=fim)

    def return_url(value, request):
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        expected = request.url_for('xml_page').path
        if parsed.scheme or parsed.netloc or parsed.path not in {expected, request.url_for('consulta_page').path} or any(c in value for c in '\r\n\\'):
            return expected
        return parsed.path + ('?' + parsed.query if parsed.query else '')

    @app.get("/xml/manifestacoes/{chave}")
    async def xml_manifestations(request: Request, chave: str, user: Annotated[WebUser, Depends(require_user)],
                                 data_inicial: str = "", data_final: str = "", codigo: str = "",
                                 pagina: int = 1, voltar: str = ""):
        if len(chave) != 44 or not chave.isascii() or not chave.isdigit():
            raise HTTPException(400, 'Chave de acesso inválida.')
        try:
            note = await run_in_threadpool(store.query_report, database_path(settings), CNPJ_PADRAO,
                password=settings.current_database_password(), note_key=chave)
            if not note['rows']:
                raise HTTPException(404, 'Nota não encontrada no banco local.')
            selected = normalizar_filtros(chave=chave, data_inicial=data_inicial, data_final=data_final, codigo=codigo)
            if pagina < 1:
                raise ValueError('Página inválida.')
            def read_events():
                conn = abrir_banco(database_path(settings), settings.current_database_password(), somente_leitura=True)
                try:
                    has_events = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manifestacoes'").fetchone()
                finally:
                    conn.close()
                if not has_events:
                    return ResultadoEventos((), 0, 1, 100)
                return consultar_eventos(database_path(settings), CNPJ_PADRAO, selected,
                    password=settings.current_database_password(), pagina=pagina)
            result = await run_in_threadpool(read_events)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return render(request, 'consulta.html', user, detail=note['rows'][0],
            note_key=chave, back_url=return_url(voltar, request),
            filtros=selected, tipos_manifestacao=TIPOS_MANIFESTACAO,
            database_ready=True, database_status_label='Banco disponível',
            database_notice=None, error=None, consulta_error=None,
            consultou=True, resultado=result, xml_keys={chave})

    @app.get("/xml/itens/{chave}")
    async def xml_items(request: Request, chave: str, user: Annotated[WebUser, Depends(require_user)],
                        item: str = "", pagina: int = 1, voltar: str = ""):
        if len(chave) != 44 or not chave.isascii() or not chave.isdigit():
            raise HTTPException(400, 'Chave de acesso inválida.')
        try:
            note = await run_in_threadpool(store.query_report, database_path(settings), CNPJ_PADRAO,
                password=settings.current_database_password(), note_key=chave)
            if not note['rows']:
                raise HTTPException(404, 'Nota não encontrada no banco local.')
            result = await run_in_threadpool(store.query_report, database_path(settings), CNPJ_PADRAO,
                password=settings.current_database_password(), kind='itens', note_key=chave,
                item_query=item, page=pagina)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return render(request, 'xml.html', user, result=result, tipo='itens', q='',
            inicio='', fim='', situacao='', item=item, error=None, ctes={},
            detail=note['rows'][0], note_key=chave, back_url=return_url(voltar, request))

    @app.get("/xml")
    async def xml_page(request: Request, user: Annotated[WebUser, Depends(require_user)],
                       tipo: str = "notas", q: str = "", data_inicial: str = "",
                       data_final: str = "", pagina: int = 1, lote: int | None = None, situacao: str = "", item: str = ""):
        error = None
        result = dict(rows=[], columns=[], total=0, page=1, pages=0, notas=0)
        history, batch = [], None
        selected = None
        ctes = {}
        try:
            selected = filters(data_inicial, data_final)
            result = await run_in_threadpool(store.query_report, database_path(settings), CNPJ_PADRAO,
                password=settings.current_database_password(), kind=tipo, query=q,
                inicio=selected.data_inicial, fim=selected.data_final, page=pagina, situacao=situacao, item_query=item)
            if tipo == 'notas':
                ctes = result.get('ctes', {})
            if user.is_admin:
                history, batch = await run_in_threadpool(store.import_history, database_path(settings),
                    password=settings.current_database_password(), batch_id=lote)
        except ValueError as exc:
            error = str(exc)
        except Exception:
            error = "Não foi possível abrir o arquivo de XMLs. Confira a configuração do banco."
        return render(request, "xml.html", user, result=result, tipo=tipo, q=q,
            inicio=selected.data_inicial_br if selected else data_inicial,
            fim=selected.data_final_br if selected else data_final,
            history=history, batch=batch, error=error, ctes=ctes, situacao=situacao, item=item)

    @app.get("/xml/importar")
    async def xml_import_page(request: Request, user: Annotated[WebUser, Depends(require_admin)],
                              lote: int | None = None, situacao_ok: int | None = None,
                              situacao_acao: str | None = None):
        batch, error = None, None
        if lote is not None:
            try:
                _, batch = await run_in_threadpool(store.import_history, database_path(settings),
                    password=settings.current_database_password(), batch_id=lote)
            except Exception:
                error = "Não foi possível consultar o resultado da importação."
        success = None
        if situacao_ok is not None:
            success = (f"Marcação manual removida de {situacao_ok} nota(s). Cancelamentos da SEFAZ permanecem válidos."
                       if situacao_acao == "remover" else f"{situacao_ok} nota(s) marcada(s) como Cancelada.")
        return render(request, "xml_import.html", user, batch=batch, error=error, success=success)

    @app.post("/xml/importar")
    async def xml_import(request: Request, user: Annotated[WebUser, Depends(require_admin)]):
        # Os parâmetros File/Form do FastAPI usam max_files=1000 antes da rota.
        # Faça o parse com o mesmo teto anunciado para o lote, apenas nesta rota.
        async with request.form(max_files=store.MAX_FILES, max_fields=1,
                                max_part_size=4096) as form:
            csrf = form.get("csrf")
            if not isinstance(csrf, str):
                raise HTTPException(403, "Token de segurança inválido.")
            validate_csrf(csrf, user, settings)
            files = form.getlist("files")
            if not files or any(not isinstance(upload, MultipartUploadFile) for upload in files):
                raise HTTPException(400, "Selecione arquivos XML ou ZIP para importar.")
            return await process_xml_import(request, user, files)

    async def process_xml_import(request, user, files):
        total = 0
        try:
            if len(files) > store.MAX_FILES:
                raise HTTPException(413, "Lote excede 5.000 arquivos.")
            with tempfile.TemporaryDirectory(prefix="nfe_xml_") as temp:
                paths = []
                for index, upload in enumerate(files):
                    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
                    if Path(filename).suffix.lower() not in (".xml", ".zip"):
                        raise HTTPException(400, "Selecione somente XMLs ou arquivos ZIP.")
                    path = Path(temp) / f"{index}.upload"
                    with path.open("wb") as out:
                        while True:
                            chunk = await upload.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > store.MAX_UPLOAD:
                                raise HTTPException(413, "Envio excede 100 MiB. Divida o lote.")
                            out.write(chunk)
                    paths.append((path, filename))
                lock = app.state.sync_lock
                if not lock.acquire(blocking=False):
                    raise HTTPException(409, "Há uma sincronização ou importação em andamento. Aguarde.")
                try:
                    summary = await run_in_threadpool(store.import_batch, database_path(settings), CNPJ_PADRAO,
                        paths, password=settings.current_database_password(), user=user.username)
                finally:
                    lock.release()
        except ValueError as exc:
            app.state.audit.write(request, user, "xml_import", "erro", reason=type(exc).__name__)
            raise HTTPException(400, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            app.state.audit.write(request, user, "xml_import", "erro", reason=type(exc).__name__)
            raise HTTPException(400, "Não foi possível importar o lote. Confira o banco e os arquivos.") from exc
        finally:
            for upload in files:
                await upload.close()
        app.state.audit.write(request, user, "xml_import", "ok", lote=summary["id"],
            importadas=summary["importadas"], cancelamentos=summary["cancelamentos"], duplicadas=summary["duplicadas"], erros=summary["erros"])
        return RedirectResponse(request.url_for("xml_import_page").include_query_params(lote=summary["id"]), status_code=303)

    @app.get("/xml/exportar")
    async def xml_export(request: Request, user: Annotated[WebUser, Depends(require_export)],
                         formato: str = "xlsx", tipo: str = "itens", q: str = "",
                         data_inicial: str = "", data_final: str = "", lote: int | None = None, situacao: str = "", item: str = "", chave: str = ""):
        if formato not in ("xlsx", "csv") or tipo not in ("notas", "itens", "retencoes"):
            raise HTTPException(400, "Formato ou relatório inválido.")
        def generate():
            selected = filters(data_inicial, data_final)
            kwargs = dict(password=settings.current_database_password(), query=q,
                          inicio=selected.data_inicial, fim=selected.data_final, export=True, situacao=situacao, item_query=item, note_key=chave)
            document_keys, emission_dates = set(), set()
            def remember(report):
                for row in store.cancellable_rows(report['rows']):
                    document_keys.add(row['Chave Acesso'])
                    emission_dates.add(row.get('Data Emissao'))
                return report
            if formato == "csv":
                report = remember(store.query_report(database_path(settings), CNPJ_PADRAO, kind=tipo, **kwargs))
                data = store.export_csv(report)
            else:
                def reports():
                    sections = (("Itens", "itens"),) if chave else (("Notas", "notas"), ("Itens", "itens"), ("Retencoes", "retencoes"))
                    for title, kind in sections:
                        yield title, remember(store.query_report(database_path(settings), CNPJ_PADRAO, kind=kind, **kwargs))
                data = store.export_excel(reports())
            return data, len(document_keys), period_label(
                emission_dates, start=selected.data_inicial, end=selected.data_final)

        try:
            data, documents, period = await run_in_threadpool(generate)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "Não foi possível gerar o relatório. Confira o banco.") from exc
        app.state.audit.write(request, user, "xml_export", "ok", formato=formato)
        media = "text/csv" if formato == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        await archive_file(request, data, filename=f"Leitor_XML.{formato}", media_type=media,
            documents=documents, period=period)
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="Leitor_XML.{formato}"'})

    @app.get("/xml/lote/{lote}/registro")
    async def xml_batch_log(request: Request, lote: int, user: Annotated[WebUser, Depends(require_admin)]):
        try:
            _, batch = await run_in_threadpool(store.import_history, database_path(settings),
                password=settings.current_database_password(), batch_id=lote)
        except Exception as exc:
            raise HTTPException(503, "Registro de importação indisponível.") from exc
        if batch is None:
            raise HTTPException(404, "Lote não encontrado.")
        data = store.export_csv(dict(columns=["arquivo", "status", "mensagem"], rows=batch["registros"]))
        await archive_file(request, data, filename=f"Importacao_XML_{lote}.csv", media_type="text/csv",
            documents=len(batch["registros"]), period="Não informado", admin_only=True)
        return Response(data, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="Importacao_XML_{lote}.csv"'})
