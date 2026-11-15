"""One app and lifespan, shared provider/Atlas connection, original routes preserved."""
import main
from minutes_module.router import Runtime, create_router
from workspace_api import create_workspace_router
from audio_processing import router as audio_router

app = main.app
if not getattr(app.state, "minutes_installed", False):
    runtime = Runtime(settings=main.settings, llm=main.llm_service)
    app.include_router(create_router(runtime))
    app.include_router(create_workspace_router(runtime))
    app.include_router(audio_router)
    app.openapi_schema = None
    app.state.minutes_installed = True
    app.state.minutes_runtime = runtime
