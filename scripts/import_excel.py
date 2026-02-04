"""
Importe des patients et consultations depuis un fichier Excel
vers la base utilisée par le site (SQLite ou PostgreSQL via DATABASE_URL).

Usage (depuis la racine du projet):
  python -m scripts.import_excel <fichier.xlsx> [feuille]
  python -m scripts.import_excel --replace <fichier.xlsx> [feuille]

  --replace  Vide les tables patients et consultations avant l'import (remplace toute la base).
  feuille    Nom ou numéro de feuille (0 = première). Par défaut : première feuille.

Format Excel attendu (première ligne = en-têtes):
  Nom, Prénom, Date de naissance, Profession, Téléphone, Autre numéro, Autre numéro #2,
  Adresse, Assurance, Matricule, Fiche #1 … Fiche #10,
  puis pour chaque consultation : Date de consultation N, Détail de la consultation, Montant de l'acte, Montant reçu
  (consultations 1 à 14).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Permet d'importer app depuis la racine du projet
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from sqlalchemy import text

from app import get_engine, get_metadata


# Correspondance nom de colonne Excel (après nettoyage lowercase) -> colonne table patients
# Format : Nom, Prénom, Date de naissance, Profession, Téléphone, Autre numéro, Autre numéro #2,
# Adresse, Assurance, Matricule, Fiche #1 … Fiche #10
COLUMN_MAPPING_PATIENTS = {
    "nom": "last_name",
    "nom du patient": "last_name",
    "prénom": "first_name",
    "prénom du patient": "first_name",
    "date de naissance": "date_of_birth",
    "date de naissance du patient": "date_of_birth",
    "profession": "profession",
    "téléphone": "phone",
    "autre numéro": "other_phone_1",
    "autre numéro #1": "other_phone_1",
    "autre numéro #2": "other_phone_2",
    "adresse": "address",
    "assurance": "insurance",
    "matricule": "matricule",
    "fiche #1": "fiche_1",
    "fiche #2": "fiche_2",
    "fiche #3": "fiche_3",
    "fiche #4": "fiche_4",
    "fiche #5": "fiche_5",
    "fiche #6": "fiche_6",
    "fiche #7": "fiche_7",
    "fiche #8": "fiche_8",
    "fiche #9": "fiche_9",
    "fiche #10": "fiche_10",
}

# Détecte "Date de consultation 1", "Date de consultation 2", … "Date de consultation 11.1" (pandas renomme les doublons)
CONSULTATION_DATE_PATTERN = re.compile(r"^date de consultation \d+(\.\d+)?$", re.IGNORECASE)


def _normalize_header(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return name.strip().lower()


def _parse_date_fr(val) -> str | None:
    """Parse une date DD/MM/YYYY ou YYYY-MM-DD, retourne YYYY-MM-DD ou None."""
    if pd.isna(val):
        return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s:
        return None
    # DD/MM/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mo, y = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
        return f"{y}-{mo}-{d}"
    # YYYY-MM-DD déjà
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return s


def _parse_float_fr(val):
    """Parse un nombre français (espace milliers, virgule décimale) ou anglais."""
    if pd.isna(val) or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("\u202f", " ").replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _row_to_patient(row: pd.Series, header_to_db: dict[str, str]) -> dict:
    out: dict = {}
    for excel_col, db_col in header_to_db.items():
        if excel_col not in row.index:
            continue
        val = row.get(excel_col)
        if pd.isna(val) or (isinstance(val, str) and val.strip().lower() in ("nan", "")):
            out[db_col] = None
            continue
        if db_col == "date_of_birth":
            out[db_col] = _parse_date_fr(val)
        elif hasattr(val, "strftime"):
            out[db_col] = val.strftime("%Y-%m-%d")
        else:
            out[db_col] = str(val).strip() or None
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--replace"]
    replace = len(args) < len(sys.argv) - 1

    if len(args) < 1:
        print("Usage: python -m scripts.import_excel [--replace] <fichier.xlsx> [feuille]")
        print("  --replace  Vide patients et consultations avant d'importer (remplace la base).")
        print("  feuille    Nom ou numéro de feuille (0 = première).")
        sys.exit(1)

    path = Path(args[0])
    if not path.is_file():
        print(f"Fichier introuvable: {path}")
        sys.exit(1)

    sheet: str | int = 0
    if len(args) >= 2:
        try:
            sheet = int(args[1])
        except ValueError:
            sheet = args[1]

    df = pd.read_excel(path, sheet_name=sheet, dtype=str)
    df = df.rename(columns=lambda c: _normalize_header(str(c)))

    # Construire le mapping en-tête Excel (nom réel) -> colonne DB pour les colonnes présentes
    header_to_patient: dict[str, str] = {}
    for col in df.columns:
        n = _normalize_header(str(col))
        if n in COLUMN_MAPPING_PATIENTS:
            header_to_patient[str(col)] = COLUMN_MAPPING_PATIENTS[n]

    if "last_name" not in header_to_patient and "first_name" not in header_to_patient:
        # Essayer des variantes courantes
        for col in df.columns:
            n = _normalize_header(col)
            if "nom" in n and "prénom" not in n:
                header_to_patient[n] = "last_name"
            elif "prénom" in n or "prenom" in n:
                header_to_patient[n] = "first_name"
        if "last_name" not in header_to_patient:
            header_to_patient[df.columns[0]] = "last_name"  # première colonne = nom par défaut
        if "first_name" not in header_to_patient and len(df.columns) > 1:
            header_to_patient[df.columns[1]] = "first_name"

    engine = get_engine()
    md = get_metadata()
    patients_table = md.tables["patients"]
    consultations_table = md.tables["consultations"]

    # Blocs consultation : "Date de consultation N" puis 3 colonnes (Détail, Montant acte, Montant reçu)
    # On utilise les indices pour gérer les colonnes dupliquées (même libellé pour chaque consultation)
    col_list = list(df.columns)
    consultation_blocks: list[tuple[int, int, int, int]] = []
    for i, col in enumerate(col_list):
        n = _normalize_header(str(col))
        if CONSULTATION_DATE_PATTERN.match(n) and i + 3 < len(col_list):
            consultation_blocks.append((i, i + 1, i + 2, i + 3))

    inserted_patients = 0
    inserted_consultations = 0

    patient_columns = {c.name for c in patients_table.c if c.name not in ("id", "created_at")}
    consult_cols = {c.name for c in consultations_table.c if c.name not in ("id", "created_at")}

    with engine.begin() as con:
        if replace:
            con.execute(text("DELETE FROM consultations"))
            con.execute(text("DELETE FROM patients"))
            print("Tables patients et consultations vidées (--replace).")

        for idx, row in df.iterrows():
            row_dict = _row_to_patient(row, header_to_patient)
            if not row_dict.get("last_name") and not row_dict.get("first_name"):
                continue
            row_dict.setdefault("last_name", "")
            row_dict.setdefault("first_name", "")
            row_dict = {k: v for k, v in row_dict.items() if k in patient_columns}

            r = con.execute(patients_table.insert().returning(patients_table.c.id), row_dict)
            patient_id = r.scalar_one()
            inserted_patients += 1

            for date_idx, detail_idx, acte_idx, recu_idx in consultation_blocks:
                date_val = row.iloc[date_idx] if date_idx < len(row) else None
                if pd.isna(date_val) or (isinstance(date_val, str) and not str(date_val).strip()):
                    continue
                consultation_date = _parse_date_fr(date_val) or str(date_val).strip()
                if not consultation_date:
                    continue
                detail_val = row.iloc[detail_idx] if detail_idx < len(row) else None
                detail = None if pd.isna(detail_val) else str(detail_val).strip() or None
                montant_acte = _parse_float_fr(row.iloc[acte_idx] if acte_idx < len(row) else None)
                montant_recu = _parse_float_fr(row.iloc[recu_idx] if recu_idx < len(row) else None)
                consult_row = {
                    "patient_id": patient_id,
                    "consultation_date": consultation_date,
                    "consultation_detail": detail,
                    "montant_acte": montant_acte,
                    "montant_recu": montant_recu,
                }
                consult_row = {k: v for k, v in consult_row.items() if k in consult_cols}
                con.execute(consultations_table.insert(), consult_row)
                inserted_consultations += 1

    print(f"Import terminé: {inserted_patients} patient(s), {inserted_consultations} consultation(s).")
    db_url = os.environ.get("DATABASE_URL", "")
    print("Base:", "PostgreSQL (DATABASE_URL)" if db_url else "SQLite (data/app.db)")


if __name__ == "__main__":
    main()
