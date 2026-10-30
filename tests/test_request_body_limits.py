import asyncio

import pytest

from nfe_consulta.web.xml_upload_limit import XmlUploadLimit


@pytest.mark.parametrize('path', ['/login', '/cadastro', '/admin/login', '/admin/agendamento'])
def test_limits_actual_bytes_without_content_length(path):
    async def run():
        async def app(scope, receive, send):
            while (await receive()).get('more_body'):
                pass
            await send({'type': 'http.response.start', 'status': 204, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
        messages = iter([
            {'type': 'http.request', 'body': b'x' * 4096, 'more_body': True},
            {'type': 'http.request', 'body': b'x' * 4097, 'more_body': False},
        ])
        async def receive():
            return next(messages)
        output = []
        async def send(message):
            output.append(message)
        await XmlUploadLimit(app)({'type': 'http', 'method': 'POST', 'path': path}, receive, send)
        return [m['status'] for m in output if m['type'] == 'http.response.start']
    assert asyncio.run(run()) == [413]


def test_login_rejects_chunked_multipart_before_route_processing(tmp_path):
    import runpy
    from pathlib import Path
    import httpx
    from nfe_consulta.web.app import create_app
    helpers = runpy.run_path(str(Path(__file__).with_name('test_web.py')))
    app = create_app(helpers['settings_web'](tmp_path))
    async def run():
        async def chunks():
            yield b'--qa\r\nContent-Disposition: form-data; name="extra"; filename="a.txt"\r\n\r\n'
            yield b'x' * 9000
            yield b'\r\n--qa--\r\n'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            return await client.post('/login', content=chunks(), headers={'content-type': 'multipart/form-data; boundary=qa'})
    assert asyncio.run(run()).status_code == 413
