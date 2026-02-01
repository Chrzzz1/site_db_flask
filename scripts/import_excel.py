"""
Importe des patients (et éventuellement des consultations) depuis un fichier Excel
vers la base utilisée par le site (SQLite ou PostgreSQL via DATABASE_URL).

Usage (depuis la racine du projet):
  python -m scripts.import_excel chemin/vers/fichier.xlsx

Ou avec mapping personnalisé des colonnes (voir COLUMN_MAPPING ci-dessous).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Permet d'importer app depuis la racine du projet
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from sqlalchemy import text

from app import get_engine, get_metadata


# Correspondance nom de colonne Excel (exact ou après nettoyage) -> colonne table patients
# Tu peux modifier selon les en-têtes de ton fichier.
COLUMN_MAPPING_PATIENTS = {
    "nom": "last_name",
    "nom du patient": "last_name",
    "prénom": "first_name",
    "prénom du patient": "first_name",
    "date de naissance": "date_of_birth",
    "date de naissance du patient": "date_of_birth",
    "profession": "profession",
    "profession du patient": "profession",
    "téléphone": "phone",
    "téléphone du patient": "phone",
    "autre numéro": "other_phone_1",
    "autre numéro #1": "other_phone_1",
    "autre numéro #2": "other_phone_2",
    "adresse": "address",
    "adresse du patient": "address",
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
    "identifiant 1": "identifiant_1",
    "identifiant 2": "identifiant_2",
    "identifiant final": "identifiant_final",
}

# Colonnes Excel optionnelles pour créer une consultation par ligne
COLUMN_MAPPING_CONSULTATION = {
    "date de consultation 1": "consultation_date",
    "date consultation 1": "consultation_date",
    "détail de la consultation": "consultation_detail",
    "détail consultation": "consultation_detail",
    "montant de l'acte": "montant_acte",
    "montant acte": "montant_acte",
    "montant reçu": "montant_recu",
}


def _normalize_header(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return name.strip().lower()


def _row_to_patient(row: pd.Series, header_to_db: dict[str, str]) -> dict:
    out: dict = {}
    for excel_col, db_col in header_to_db.items():
        if excel_col in row.index:
            val = row.get(excel_col)
            if pd.isna(val) or (isinstance(val, str) and val.strip().lower() in ("nan", "")):
                val = None
            elif hasattr(val, "strftime"):
                val = val.strftime("%Y-%m-%d")
            else:
                val = str(val).strip() or None
            out[db_col] = val
    return out


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.import_excel <fichier.xlsx> [feuille]")
        print("  feuille = nom de la feuille ou numéro (0 = première). Par défaut: première feuille.")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"Fichier introuvable: {path}")
        sys.exit(1)

    sheet: str | int = 0
    if len(sys.argv) >= 3:
        try:
            sheet = int(sys.argv[2])
        except ValueError:
            sheet = sys.argv[2]

    df = pd.read_excel(path, sheet_name=sheet, dtype=str)
    df = df.rename(columns=lambda c: _normalize_header(str(c)))

    # Construire le mapping en-tête Excel -> colonne DB pour les colonnes présentes
    header_to_patient: dict[str, str] = {}
    for col in df.columns:
        n = _normalize_header(col)
        if n in COLUMN_MAPPING_PATIENTS:
            header_to_patient[n] = COLUMN_MAPPING_PATIENTS[n]

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

    # Colonnes consultation présentes dans le fichier
    header_to_consult: dict[str, str] = {}
    for col in df.columns:
        n = _normalize_header(col)
        if n in COLUMN_MAPPING_CONSULTATION:
            header_to_consult[n] = COLUMN_MAPPING_CONSULTATION[n]

    inserted_patients = 0
    inserted_consultations = 0

    patient_columns = {c.name for c in patients_table.c if c.name not in ("id", "created_at")}
    with engine.begin() as con:
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

            if header_to_consult:
                consult_row: dict = {"patient_id": patient_id}
                for excel_col, db_col in header_to_consult.items():
                    if excel_col in row.index:
                        val = row.get(excel_col)
                        if pd.isna(val):
                            val = None
                        elif hasattr(val, "isoformat") or (hasattr(val, "strftime")):
                            val = val.strftime("%Y-%m-%d") if hasattr(val, "strftime") else str(val)
                        else:
                            val = str(val).strip() or None
                        consult_row[db_col] = val
                if consult_row.get("consultation_date"):
                    consult_cols = {c.name for c in consultations_table.c if c.name not in ("id", "created_at")}
                    consult_row = {k: v for k, v in consult_row.items() if k in consult_cols}
                    con.execute(consultations_table.insert(), consult_row)
                    inserted_consultations += 1

    print(f"Import terminé: {inserted_patients} patient(s), {inserted_consultations} consultation(s).")
    db_url = os.environ.get("DATABASE_URL", "")
    print("Base:", "PostgreSQL (DATABASE_URL)" if db_url else "SQLite (data/app.db)")


if __name__ == "__main__":
    main()
