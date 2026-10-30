"""Limite do corpo antes do parser multipart, inclusive sem Content-Length."""
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from nfe_consulta.web.xml_store import MAX_UPLOAD


class _UploadTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, "Requisição excede o limite permitido.")


class XmlUploadLimit:
    def __init__(self, app, max_upload_bytes=2 * 1024 * 1024):
        self.app = app
        self.max_upload_bytes = max_upload_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        if path == '/xml/importar':
            limit = MAX_UPLOAD + 2 * 1024 * 1024
            detail = 'Envio excede 100 MiB. Divida o lote.'
        elif path == '/excel':
            limit = self.max_upload_bytes + 65536
            detail = 'Requisição excede o limite permitido.'
        elif path == '/admin/notas/situacao':
            limit = 65536
            detail = 'Requisição excede o limite permitido.'
        elif path in {'/login', '/cadastro'} or path.startswith(('/admin/', '/atualizar')):
            limit = 8192
            detail = 'Requisição excede o limite permitido.'
        else:
            return await self.app(scope, receive, send)
        size = 0
        started = False
        exceeded = False
        replaced = False
        async def limited_receive():
            nonlocal size, exceeded
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    exceeded = True
                    raise _UploadTooLarge()
            return message
        async def tracked_send(message):
            nonlocal started, replaced
            if exceeded:
                if not replaced:
                    replaced = True
                    await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        try:
            await self.app(scope, limited_receive, tracked_send)
        # BaseHTTPMiddleware pode envolver a rejeição em um ExceptionGroup.
        except* _UploadTooLarge:
            if started:
                raise
            await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)
