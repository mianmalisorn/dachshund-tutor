import hashlib
import os
from dataclasses import replace
from urllib.parse import urlsplit
import logging
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from .config import ROOT, Settings
from .db import init_db
from .routers import analytics, quizzes, finance, attendance, admin, auth, files, student, mascot, teaching, progress, schedule, reminders, personal, productivity, video, video_materials
from .web import render
from .request_limits import RequestLimitMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not any((settings.livekit_ws_url, settings.livekit_http_url,
                settings.livekit_api_key, settings.livekit_api_secret)):
        settings = replace(settings,
                           livekit_ws_url=os.getenv('LIVEKIT_WS_URL', ''),
                           livekit_http_url=os.getenv('LIVEKIT_HTTP_URL', ''),
                           livekit_api_key=os.getenv('LIVEKIT_API_KEY', ''),
                           livekit_api_secret=os.getenv('LIVEKIT_API_SECRET', ''))
    settings.upload_root.mkdir(parents=True, exist_ok=True)
    # SQLite path must exist before SQLAlchemy tries to open a connection.
    if settings.database_url.startswith('sqlite:///') and ':memory:' not in settings.database_url:
        from pathlib import Path
        Path(settings.database_url[len('sqlite:///'):]).parent.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title='Tutor', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine, app.state.sessionmaker = init_db(settings.database_url)
    app.state.video_provider = None
    video_host = urlsplit(settings.livekit_ws_url).hostname
    video_connect_sources = ''
    if (video_host and urlsplit(settings.livekit_ws_url).scheme == 'wss'
            and urlsplit(settings.livekit_http_url).scheme == 'https'
            and video_host == urlsplit(settings.livekit_http_url).hostname
            and all((settings.livekit_api_key, settings.livekit_api_secret))):
        video_connect_sources = f' wss://{video_host} https://{video_host}'
        if video_host.endswith('.livekit.cloud'):
            video_connect_sources += ' wss://*.livekit.cloud https://*.livekit.cloud'

    @app.on_event('startup')
    async def open_video_provider():
        ws = urlsplit(settings.livekit_ws_url)
        http = urlsplit(settings.livekit_http_url)
        if app.state.video_provider is None and all((
                settings.livekit_ws_url, settings.livekit_http_url,
                settings.livekit_api_key, settings.livekit_api_secret)) and (
                ws.scheme == 'wss' and http.scheme == 'https' and ws.hostname
                and ws.hostname == http.hostname and not ws.query and not http.query
                and not ws.fragment and not http.fragment and not ws.username
                and not http.username):
            from .livekit_provider import LiveKitProvider
            app.state.video_provider = LiveKitProvider(
                settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret)

    @app.on_event('shutdown')
    async def close_video_provider():
        if app.state.video_provider is not None:
            await app.state.video_provider.close()
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key,
                       session_cookie=('cattutor_demo_' + hashlib.sha256(str(settings.upload_root.resolve()).encode()).hexdigest()[:12]) if settings.demo_mode else 'cattutor_session',
                       https_only=settings.secure_cookies, same_site='lax', max_age=60 * 60 * 12)

    @app.middleware('http')
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # Starlette's slash redirects use the proxy's internal Host. Keep local
        # redirects on the browser's origin without trusting X-Forwarded-Host.
        if response.status_code in (307, 308) and 'location' in response.headers:
            location = urlsplit(response.headers['location'])
            if (location.scheme in ('http', 'https') and
                    location.netloc == request.url.netloc and
                    location.path.startswith('/') and not location.path.startswith('//')):
                response.headers['location'] = location.path + ('?' + location.query if location.query else '')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if not request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        connect_sources = video_connect_sources if request.url.path.startswith('/video/meetings/') else ''
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'" + connect_sources +
            "; object-src 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    app.mount('/static', StaticFiles(directory=str(ROOT / 'app' / 'static')), name='static')
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(schedule.router)
    app.include_router(attendance.router)
    app.include_router(finance.router)
    app.include_router(quizzes.router)
    app.include_router(analytics.router)
    app.include_router(reminders.router)
    app.include_router(personal.router)
    app.include_router(productivity.router)
    app.include_router(progress.router)
    app.include_router(teaching.router)
    app.include_router(mascot.router)
    app.include_router(student.router)
    app.include_router(video.router)
    app.include_router(video_materials.router)
    app.include_router(video.page_router)
    app.include_router(files.router)
    app.add_middleware(RequestLimitMiddleware, max_bytes=settings.max_request_mb*1024*1024)

    @app.get('/favicon.ico', include_in_schema=False)
    def favicon():
        return FileResponse(ROOT / 'app' / 'static' / 'favicon.ico', media_type='image/x-icon')

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.exception_handler(HTTPException)
    async def errors(request: Request, exc: HTTPException):
        if request.url.path.startswith('/api/video/'):
            return JSONResponse({'detail': str(exc.detail or 'Ошибка')}, status_code=exc.status_code)
        if request.url.path.startswith('/admin/productivity/') and 'application/json' in request.headers.get('accept', ''):
            return JSONResponse({'detail': str(exc.detail or 'Операцию не удалось выполнить. Проверьте данные и попробуйте снова.')}, status_code=exc.status_code)
        if exc.status_code == 401:
            return RedirectResponse('/login?notice=login_required', status_code=303)
        if exc.status_code == 423:
            return RedirectResponse('/account?notice=change_password', status_code=303)
        return _error_response(request, exc)

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(OSError)
    async def storage_error(request: Request, exc):
        # Do not log SQL parameters, filesystem paths or private form contents.
        logging.getLogger(__name__).error('Storage request failed: %s', type(exc).__name__)
        code = 409 if isinstance(exc, IntegrityError) else 503
        detail = ('Данные изменились или такая запись уже существует. Обновите страницу.'
                  if code == 409 else
                  'Хранилище временно недоступно. Проверьте результат в кабинете перед повторной отправкой. '
                  'Если ошибка повторяется, сообщите администратору.')
        if request.url.path.startswith('/api/video/'):
            return JSONResponse({'detail': detail}, status_code=code)
        return _error_response(request, HTTPException(code, detail))

    return app


def _error_response(request, exc):
    response = render(request, 'error.html', error_code=exc.status_code,
                      error_text=str(exc.detail or 'Ошибка'), user=None,
                      review_recovery=getattr(request.state,'review_recovery',None))
    response.status_code = exc.status_code
    return response


# Run uvicorn app.main:create_app --factory; importing has no side effects.
