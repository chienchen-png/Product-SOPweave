from app import create_app
from agent_runtime.api import create_agent_api


flask_app = create_app()
app = create_agent_api(flask_app=flask_app)
