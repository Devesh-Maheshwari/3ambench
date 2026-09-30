"""ASGI app: `uvicorn alertforge_env.server.app:app` with the parent of alertforge_env on PYTHONPATH (the Dockerfile uses /app/env)."""

import os

from openenv.core.env_server.http_server import create_app

from alertforge_env.models import AlertForgeAction, AlertForgeObservation
from alertforge_env.server.alertforge_environment import AlertForgeEnvironment

app = create_app(AlertForgeEnvironment, AlertForgeAction, AlertForgeObservation, env_name="alertforge_env",
                 max_concurrent_envs=int(os.environ.get("AF_MAX_CONCURRENT_ENVS", "16")))


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
