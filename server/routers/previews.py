import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from server.config import SNAPSHOTS_DIR

router = APIRouter(tags=["Preview"])


def _conversation_dir(user: str, conversation_id: str) -> Path:
    return SNAPSHOTS_DIR / user / conversation_id


def _sort_key_by_number(file_path: Path) -> int:
    match = re.search(r"_(\d+)\.html", file_path.name)
    return int(match.group(1)) if match else -1


def _list_html_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.html"), key=_sort_key_by_number) if directory.exists() else []


@router.get("/list/{user}/{conversation_id}")
async def list_html_files(user: str, conversation_id: str):
    conversation_dir = _conversation_dir(user, conversation_id)
    source_dirs = {
        "alphapai": conversation_dir / "alphapai",
        "gangtise": conversation_dir / "gangtise",
    }

    if not any(path.exists() for path in source_dirs.values()):
        raise HTTPException(status_code=404, detail="Conversation not found")

    response_payload = []
    for source, directory in source_dirs.items():
        files = _list_html_files(directory)
        response_payload.append(
            [
                {
                    "name": file_path.name,
                    "src": f"/preview/{user}/{conversation_id}/{source}/{file_path.name}",
                }
                for file_path in files
            ]
        )

    if not any(response_payload):
        raise HTTPException(status_code=404, detail="No HTML file found")

    return JSONResponse(content=response_payload)


@router.get("/preview/{user}/{conversation_id}/{source}/{filename}")
async def preview_file(user: str, conversation_id: str, source: str, filename: str):
    html_path = _conversation_dir(user, conversation_id) / source / filename
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(html_path, media_type="text/html")


@router.delete("/delete/{user}/{conversation_id}")
async def delete_html_dir(user: str, conversation_id: str):
    conversation_dir = _conversation_dir(user, conversation_id)

    if not conversation_dir.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        shutil.rmtree(conversation_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error deleting directory: {exc}") from exc

    return JSONResponse(content={"status": "deleted"})
