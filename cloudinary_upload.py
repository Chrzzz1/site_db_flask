"""
Upload de fiches numérisées vers Cloudinary.
Nécessite : CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
"""
from __future__ import annotations

import os
from io import BytesIO

ALLOWED_MIME = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
MAX_SIZE_BYTES = 15 * 1024 * 1024  # 15 Mo


def upload_fiche_to_cloudinary(
    file_data: bytes,
    filename: str,
    mime_type: str,
    patient_name: str = "",
) -> str | None:
    """
    Envoie un fichier vers Cloudinary et renvoie l'URL publique.
    Retourne None en cas d'erreur ou si non configuré.
    """
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

    if not cloud_name or not api_key or not api_secret:
        return None
    if mime_type not in ALLOWED_MIME:
        return None
    if len(file_data) > MAX_SIZE_BYTES:
        return None

    try:
        import cloudinary
        import cloudinary.uploader
    except ImportError:
        return None

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )

    try:
        # Nom du fichier : nom patient + nom original
        safe_name = "".join(c for c in (patient_name or "fiche") if c.isalnum() or c in " -_")
        safe_name = (safe_name[:50] + "_" + filename).strip() or filename or "fiche"
        safe_name = safe_name.replace(" ", "_")

        # Upload vers le dossier fiches_patients
        result = cloudinary.uploader.upload(
            BytesIO(file_data),
            folder="fiches_patients",
            public_id=safe_name,
            resource_type="auto",  # auto-détecte PDF ou image
            overwrite=False,
            unique_filename=True,
        )

        return result.get("secure_url")
    except Exception:
        return None
