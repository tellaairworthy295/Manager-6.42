# Project Structure

## Runtime entrypoints

- `app.py`: thin FastAPI entrypoint.
- `dramatiq_app.py`: thin Dramatiq worker entrypoint.

## Server wiring

- `server/application.py`: app factory and lifespan management.
- `server/scheduler.py`: APScheduler registration.
- `server/jobs.py`: scheduled job implementations.
- `server/mcp_tools.py`: MCP tool registration.
- `server/routers/`: internal routers owned by the server runtime.
- `server/config.py`: shared runtime paths and constants.

## Domain code

- `server/api/`: external API routers for news, stocks, records, and agent flows.
- `tasks/`: Dramatiq background jobs.
- `scraper/`: scraper implementations and scrape orchestration helpers.
- `image_process/`: OCR and post-processing flow for stock images.
- `utils/`: shared infrastructure utilities such as database, Redis, logging, and validators.

## Data and assets

- `json/`: selectors, locators, and runtime configuration.
- `snapshoots/`: generated HTML snapshots.
- `images/`, `excel/`, `logs/`: generated artifacts.

## Legacy standalone scripts

Several root-level scripts still exist for one-off operations. They can be migrated into a dedicated `scripts/` directory later without affecting the new runtime structure.
