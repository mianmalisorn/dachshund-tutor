"""Bound request bytes before multipart parsing, including chunked requests."""
from tempfile import SpooledTemporaryFile
from starlette.responses import PlainTextResponse


class RequestLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST','PUT','PATCH'}:
            return await self.app(scope,receive,send)
        with SpooledTemporaryFile(max_size=1024*1024) as body:
            total=0
            try:
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect':
                        return
                    chunk=message.get('body',b'')
                    total+=len(chunk)
                    if total>self.max_bytes:
                        response=PlainTextResponse('Слишком большой запрос. Уменьшите число или размер файлов.',status_code=413)
                        return await response(scope,receive,send)
                    body.write(chunk)
                    if not message.get('more_body',False):
                        break
                body.seek(0)
                remaining=total
                delivered=False
                async def bounded_receive():
                    nonlocal remaining, delivered
                    if delivered:
                        return await receive()
                    data=body.read(64*1024)
                    remaining-=len(data)
                    delivered=remaining==0
                    return {'type':'http.request','body':data,'more_body':not delivered}
                await self.app(scope,bounded_receive,send)
            finally:
                form=scope.pop('parsed_form',None)
                if form is not None:
                    await form.close()
