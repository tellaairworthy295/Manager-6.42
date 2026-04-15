import uvicorn

from server import create_app
from server.config import APP_HOST, APP_PORT

app = create_app()


if __name__ == "__main__":
    uvicorn.run("app:app", host=APP_HOST, port=APP_PORT)
