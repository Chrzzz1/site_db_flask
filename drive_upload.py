"""
Upload de fiches numérisées vers Google Drive.
Nécessite : GOOGLE_DRIVE_CREDENTIALS_JSON (chemin vers le fichier JSON du compte de service)
            GOOGLE_DRIVE_FOLDER_ID (optionnel, ID du dossier cible dans Drive)
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any

ALLOWED_MIME = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
MAX_SIZE_BYTES = 15 * 1024 * 1024  # 15 Mo


def _get_credentials_path() -> str | None:
    path = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_JSON", "").strip()
    if path:
        return path
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    return path or None


def upload_fiche_to_drive(
    file_data: bytes,
    filename: str,
    mime_type: str,
    patient_name: str = "",
) -> str | None:
    """
    Envoie un fichier vers Google Drive et renvoie le lien de consultation (webViewLink).
    Retourne None en cas d'erreur ou si non configuré.
    """
    creds_path = _get_credentials_path()
    if not creds_path or not Path(creds_path).exists():
        return None
    if mime_type not in ALLOWED_MIME:
        return None
    if len(file_data) > MAX_SIZE_BYTES:
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
    except ImportError:
        return None

    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None

    try:
        credentials = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        service = build("drive", "v3", credentials=credentials)

        # Nom du fichier : nom patient + nom original si dispo
        safe_name = "".join(c for c in (patient_name or "fiche") if c.isalnum() or c in " -_")
        safe_name = (safe_name[:50] + "_" + filename).strip() or filename or "fiche"

        metadata: dict[str, Any] = {"name": safe_name}
        if folder_id:
            metadata["parents"] = [folder_id]

        media = MediaIoBaseUpload(
            BytesIO(file_data),
            mimetype=mime_type,
            resumable=False,
        )

        file_obj = (
            service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id, webViewLink",
            )
            .execute()
        )

        file_id = file_obj.get("id")
        if not file_id:
            return None

        # Permission : toute personne avec le lien peut voir
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()

        return file_obj.get("webViewLink")
    except Exception:
        return None
