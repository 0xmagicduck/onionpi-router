from __future__ import annotations

import mimetypes
import os
import secrets
import shutil
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from .context import RouteContext


class FolderRequest(BaseModel):
    parent: str = Field(default="", max_length=500)
    name: str = Field(min_length=1, max_length=120)


#: Boundaries, part headers and the `path` field of the import form. Generous:
#: it only decides when a declared Content-Length is refused without reading it.
MULTIPART_ENVELOPE_BYTES = 8 * 1024

#: Ceiling for the *text* fields of the import form, which is all `max_part_size`
#: governs: a file part is streamed to a spooled temporary file and never
#: measured against it. Passing the upload maximum here let `path` — one string
#: bounded to 500 characters once it reaches the handler — be accumulated in a
#: bytearray up to a gigabyte first, which is the whole memory of the appliance.
MAX_FORM_FIELD_BYTES = 64 * 1024


def upload_body_budget(free_bytes: int, reserve_bytes: int, maximum: int) -> int:
    """Bytes of an import the appliance may hold at once, given its free space.

    Halved because the spooled copy and the finished file exist together, on
    the same filesystem, for as long as the last chunk takes to land.
    """
    return min(maximum, max(0, (free_bytes - reserve_bytes) // 2))


def create_router(context: RouteContext) -> APIRouter:
    router = APIRouter()
    settings = context.settings
    database = context.services.database
    current_session = context.current_session
    csrf_session = context.csrf_session

    def safe_path(relative: str, require_exists: bool = False) -> Path:
        cleaned = relative.strip().lstrip("/")
        try:
            candidate = (settings.shared_dir / cleaned).resolve()
            inside = (
                os.path.commonpath([str(settings.shared_dir), str(candidate)])
                == str(settings.shared_dir)
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Chemin invalide") from error
        if not inside:
            raise HTTPException(status_code=400, detail="Chemin invalide")
        if require_exists and not candidate.exists():
            raise HTTPException(status_code=404, detail="Fichier introuvable")
        return candidate

    def clean_filename(value: str) -> str:
        name = unicodedata.normalize("NFC", Path(value).name).strip()
        name = "".join(character for character in name if character.isprintable())
        if not name or name in {".", ".."} or name.startswith("."):
            raise HTTPException(status_code=400, detail="Nom de fichier invalide")
        return name[:180]

    def file_item(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "name": path.name,
            "path": path.relative_to(settings.shared_dir).as_posix(),
            "is_directory": path.is_dir(),
            "size": 0 if path.is_dir() else stat.st_size,
            "modified_at": int(stat.st_mtime),
            "mime": (
                "inode/directory"
                if path.is_dir()
                else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            ),
        }

    @router.get("/api/v1/files")
    def list_files(
        path: str = Query(default="", max_length=500),
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        directory = safe_path(path, require_exists=True)
        if not directory.is_dir():
            raise HTTPException(
                status_code=400, detail="Ce chemin n’est pas un dossier"
            )
        items = [
            file_item(item)
            for item in directory.iterdir()
            if not item.name.startswith(".") and not item.is_symlink()
        ]
        items.sort(key=lambda item: (not item["is_directory"], item["name"].casefold()))
        usage = shutil.disk_usage(settings.shared_dir)
        storage = (
            {
                "used": 9_400_000_000,
                "total": 32_000_000_000,
                "free": 22_600_000_000,
            }
            if settings.demo_mode
            else {"used": usage.used, "total": usage.total, "free": usage.free}
        )
        return {
            "path": (
                ""
                if directory == settings.shared_dir
                else directory.relative_to(settings.shared_dir).as_posix()
            ),
            "items": items,
            "storage": storage,
        }

    def upload_budget(request: Request) -> int:
        """Bytes this import may occupy, refused before anything is buffered.

        The multipart parser spools the whole body to temporary storage before
        the handler ever sees it, so a reserve consulted afterwards protects
        nothing: the card is already full by the time the 507 is answered. The
        declared length is judged here, and BodyLimitMiddleware holds the same
        budget against a client that declares nothing at all.
        """
        limit = upload_body_budget(
            shutil.disk_usage(settings.shared_dir).free,
            settings.storage_reserve_bytes,
            settings.max_upload_bytes,
        )
        if limit <= 0:
            raise HTTPException(status_code=507, detail="Espace disque insuffisant")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit + MULTIPART_ENVELOPE_BYTES:
            raise HTTPException(
                status_code=413 if limit == settings.max_upload_bytes else 507,
                detail=(
                    "Fichier trop volumineux"
                    if limit == settings.max_upload_bytes
                    else "Espace disque insuffisant"
                ),
            )
        return limit

    @router.post("/api/v1/files/upload", status_code=201)
    async def upload_file(
        request: Request,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        limit = upload_budget(request)
        form = await request.form(
            max_files=1,
            max_fields=2,
            max_part_size=MAX_FORM_FIELD_BYTES,
        )
        file = form.get("file")
        path_value = form.get("path", "")
        if not isinstance(file, UploadFile) or not isinstance(path_value, str):
            raise HTTPException(
                status_code=400, detail="Formulaire d’import invalide"
            )
        directory = safe_path(path_value, require_exists=True)
        if not directory.is_dir():
            raise HTTPException(status_code=400, detail="Destination invalide")
        filename = clean_filename(file.filename or "")
        target = directory / filename
        if target.exists():
            raise HTTPException(
                status_code=409, detail="Un fichier porte déjà ce nom"
            )
        temporary = directory / f".upload-{secrets.token_hex(10)}"
        total = 0
        try:
            with temporary.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(
                            status_code=(
                                413 if limit == settings.max_upload_bytes else 507
                            ),
                            detail=(
                                "Fichier trop volumineux"
                                if limit == settings.max_upload_bytes
                                else "Espace disque insuffisant"
                            ),
                        )
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()
        database.add_activity(
            "upload", f"{session['display_name']} a importé {filename}"
        )
        return {"item": file_item(target)}

    @router.post("/api/v1/files/folders", status_code=201)
    def create_folder(
        payload: FolderRequest,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        parent = safe_path(payload.parent, require_exists=True)
        target = parent / clean_filename(payload.name)
        try:
            target.mkdir(mode=0o750)
        except FileExistsError as error:
            raise HTTPException(
                status_code=409, detail="Ce dossier existe déjà"
            ) from error
        database.add_activity(
            "folder", f"{session['display_name']} a créé {target.name}"
        )
        return {"item": file_item(target)}

    @router.get("/api/v1/files/download")
    def download_file(
        path: str = Query(max_length=500),
        _: dict[str, Any] = Depends(current_session),
    ) -> FileResponse:
        target = safe_path(path, require_exists=True)
        if not target.is_file():
            raise HTTPException(
                status_code=400,
                detail="Téléchargement de dossier non pris en charge",
            )
        return FileResponse(
            target, filename=target.name, media_type="application/octet-stream"
        )

    @router.delete("/api/v1/files", status_code=204)
    def delete_file(
        path: str = Query(min_length=1, max_length=500),
        session: dict[str, Any] = Depends(csrf_session),
    ) -> Response:
        target = safe_path(path, require_exists=True)
        if target == settings.shared_dir:
            raise HTTPException(status_code=400, detail="Suppression interdite")
        try:
            target.rmdir() if target.is_dir() else target.unlink()
        except OSError as error:
            raise HTTPException(
                status_code=409, detail="Le dossier doit être vide"
            ) from error
        database.add_activity(
            "delete", f"{session['display_name']} a supprimé {target.name}"
        )
        return Response(status_code=204)

    return router
