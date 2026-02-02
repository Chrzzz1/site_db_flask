from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    text,
)
from sqlalchemy.engine import Engine


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

_ENGINE: Engine | None = None


def _normalize_date_input(v: str) -> str:
    """
    Accepte:
    - YYYY-MM-DD (ex: 1991-05-14)
    - YYYY/MM/DD (ex: 1991/05/14 ou 1968/12/21) -> converti en YYYY-MM-DD
    - DD/MM/YYYY (ex: 14/05/1991) -> converti en YYYY-MM-DD
    Sinon renvoie la valeur nettoyée telle quelle.
    """
    v = (v or "").strip()
    if not v:
        return ""
    # YYYY/MM/DD ou YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", v)
    if m:
        yyyy, mm, dd = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        return f"{yyyy}-{mm}-{dd}"
    # DD/MM/YYYY
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        dd, mm, yyyy = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
        return f"{yyyy}-{mm}-{dd}"
    return v


def _date_from_parts(day: str, month: str, year: str) -> str:
    """Construit YYYY-MM-DD à partir de jour, mois, année (sélecteurs)."""
    day, month, year = (day or "").strip(), (month or "").strip(), (year or "").strip()
    if not day or not month or not year:
        return ""
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def _parse_date_parts(date_str: str) -> tuple[str, str, str]:
    """Extrait (jour, mois, année) depuis YYYY-MM-DD pour pré-remplir les selects."""
    if not date_str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str.strip()):
        return ("", "", "")
    y, m, d = date_str.strip().split("-")
    return (d.lstrip("0") or "1", m.lstrip("0") or "1", y)


def create_app() -> Flask:
    app = Flask(__name__)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    @app.get("/")
    def index() -> str:
        last_name = (request.args.get("last_name") or "").strip()
        first_name = (request.args.get("first_name") or "").strip()
        matricule = (request.args.get("matricule") or "").strip()
        date_day = (request.args.get("date_day") or "").strip()
        date_month = (request.args.get("date_month") or "").strip()
        date_year = (request.args.get("date_year") or "").strip()
        if date_day and date_month and date_year:
            date_of_birth = _date_from_parts(date_day, date_month, date_year)
        elif date_month and date_year:
            date_of_birth = f"{date_year}-{date_month.zfill(2)}-%"
        elif date_year:
            date_of_birth = f"{date_year}-%"
        else:
            date_of_birth = _normalize_date_input(request.args.get("date_of_birth") or "")
        if date_of_birth and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_of_birth):
            date_day, date_month, date_year = _parse_date_parts(date_of_birth)
        phone = (request.args.get("phone") or "").strip()
        try:
            limit = int(request.args.get("limit") or "50")
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 200))

        rows: list[dict[str, Any]] = []
        sql_used = ""
        params_used: list[Any] = []

        base_sql = """
            SELECT
                p.id,
                p.last_name,
                p.first_name,
                p.date_of_birth,
                p.profession,
                p.phone,
                p.other_phone_1,
                p.other_phone_2,
                p.address,
                p.insurance,
                p.matricule,
                p.identifiant_1,
                p.identifiant_2,
                p.identifiant_final,
                (
                    SELECT c.consultation_date
                    FROM consultations c
                    WHERE c.patient_id = p.id
                    ORDER BY c.consultation_date DESC, c.id DESC
                    LIMIT 1
                ) AS last_consultation_date
            FROM patients p
            ORDER BY p.id DESC
            LIMIT :limit
        """.strip()

        where_parts: list[str] = []
        params: dict[str, Any] = {"limit": limit}

        if last_name:
            params["last_name_like"] = f"%{last_name}%"
            where_parts.append("lower(p.last_name) LIKE lower(:last_name_like)")

        if first_name:
            params["first_name_like"] = f"%{first_name}%"
            where_parts.append("lower(p.first_name) LIKE lower(:first_name_like)")

        if matricule:
            params["matricule_like"] = f"%{matricule}%"
            where_parts.append("p.matricule LIKE :matricule_like")

        if phone:
            params["phone_like"] = f"%{phone}%"
            where_parts.append(
                "(p.phone LIKE :phone_like OR p.other_phone_1 LIKE :phone_like OR p.other_phone_2 LIKE :phone_like)"
            )

        if date_of_birth:
            # Date complète YYYY-MM-DD → match exact ou avec heure (1993-08-03 00:00:00)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_of_birth):
                params["dob_eq"] = date_of_birth
                params["dob_like_start"] = date_of_birth + "%"
                y, m, d = date_of_birth.split("-")
                params["dob_ddmmyyyy"] = f"{d}/{m}/{y}"
                where_parts.append(
                    "(TRIM(p.date_of_birth) = :dob_eq OR p.date_of_birth LIKE :dob_like_start OR p.date_of_birth = :dob_ddmmyyyy)"
                )
            elif "%" in date_of_birth:
                params["dob_like"] = date_of_birth
                where_parts.append("p.date_of_birth LIKE :dob_like")
            else:
                params["dob_like"] = f"%{date_of_birth}%"
                where_parts.append("p.date_of_birth LIKE :dob_like")

        if where_parts:
            sql_used = (
                base_sql.replace(
                    "FROM patients p",
                    "FROM patients p\nWHERE " + "\n  AND ".join(where_parts),
                )
            )
            params_used = [params]
            with get_engine().connect() as con:
                res = con.execute(text(sql_used), params)
                rows = [dict(r) for r in res.mappings().all()]
        else:
            # Aucun filtre: section résultats vide par défaut
            sql_used = ""
            params_used = []
            rows = []

        return render_template(
            "index.html",
            last_name=last_name,
            first_name=first_name,
            matricule=matricule,
            date_of_birth=date_of_birth,
            date_day=date_day,
            date_month=date_month,
            date_year=date_year,
            phone=phone,
            limit=limit,
            rows=rows,
            sql_used=sql_used,
            params_used=params_used,
        )

    @app.get("/patients/<int:patient_id>")
    def patient_detail(patient_id: int) -> str:
        error = (request.args.get("error") or "").strip()
        with get_engine().connect() as con:
            patient = con.execute(
                text(
                    """
                    SELECT
                        id,
                        last_name,
                        first_name,
                        date_of_birth,
                        profession,
                        phone,
                        other_phone_1,
                        other_phone_2,
                        address,
                        insurance,
                        matricule,
                        fiche_1, fiche_2, fiche_3, fiche_4, fiche_5,
                        fiche_6, fiche_7, fiche_8, fiche_9, fiche_10,
                        identifiant_1,
                        identifiant_2,
                        identifiant_final,
                        created_at
                    FROM patients
                    WHERE id = :patient_id
                    """.strip()
                ),
                {"patient_id": patient_id},
            ).mappings().first()

            consultations = con.execute(
                text(
                    """
                    SELECT id, consultation_date, consultation_detail, montant_acte, montant_recu
                    FROM consultations
                    WHERE patient_id = :patient_id
                    ORDER BY consultation_date DESC, id DESC
                    LIMIT 200
                    """.strip()
                ),
                {"patient_id": patient_id},
            ).mappings().all()

        if patient is None:
            return render_template(
                "patient.html",
                patient=None,
                consultations=[],
                fiches=[],
                error=error,
            )

        fiches = [(i, patient[f"fiche_{i}"]) for i in range(1, 11) if patient[f"fiche_{i}"]]
        return render_template(
            "patient.html",
            patient=patient,
            consultations=consultations,
            fiches=fiches,
            error=error,
        )

    @app.post("/patients/<int:patient_id>/consultations")
    def add_consultation(patient_id: int):
        consultation_date = (request.form.get("consultation_date") or "").strip()
        consultation_detail = (request.form.get("consultation_detail") or "").strip() or None
        montant_acte_raw = (request.form.get("montant_acte") or "").strip()
        montant_recu_raw = (request.form.get("montant_recu") or "").strip()

        if not consultation_date:
            return redirect(
                url_for(
                    "patient_detail",
                    patient_id=patient_id,
                    error="La date de consultation est obligatoire.",
                )
            )

        def parse_amount(v: str) -> float | None:
            if not v:
                return None
            v = v.replace(",", ".")
            return float(v)

        try:
            montant_acte = parse_amount(montant_acte_raw)
            montant_recu = parse_amount(montant_recu_raw)
        except ValueError:
            return redirect(
                url_for(
                    "patient_detail",
                    patient_id=patient_id,
                    error="Montant invalide (utilise un nombre, ex: 50 ou 50.5).",
                )
            )

        with get_engine().begin() as con:
            exists = con.execute(
                text("SELECT 1 FROM patients WHERE id = :id"),
                {"id": patient_id},
            ).first()
            if not exists:
                return redirect(url_for("index"))

            con.execute(
                text(
                    """
                    INSERT INTO consultations (
                        patient_id, consultation_date, consultation_detail, montant_acte, montant_recu
                    ) VALUES (:patient_id, :consultation_date, :consultation_detail, :montant_acte, :montant_recu)
                    """.strip()
                ),
                {
                    "patient_id": patient_id,
                    "consultation_date": consultation_date,
                    "consultation_detail": consultation_detail,
                    "montant_acte": montant_acte,
                    "montant_recu": montant_recu,
                },
            )

        return redirect(url_for("patient_detail", patient_id=patient_id))

    @app.get("/patients/new")
    def add_patient_form() -> str:
        return render_template(
            "add_patient.html",
            error="",
            date_day="",
            date_month="",
            date_year="",
        )

    @app.post("/patients/new")
    def add_patient_submit():
        last_name = (request.form.get("last_name") or "").strip()
        first_name = (request.form.get("first_name") or "").strip()
        date_day = (request.form.get("date_day") or "").strip()
        date_month = (request.form.get("date_month") or "").strip()
        date_year = (request.form.get("date_year") or "").strip()
        if date_day and date_month and date_year:
            date_of_birth = _date_from_parts(date_day, date_month, date_year)
        else:
            date_of_birth = (request.form.get("date_of_birth") or "").strip() or None
            date_of_birth = _normalize_date_input(date_of_birth) if date_of_birth else None

        if not last_name or not first_name:
            return render_template(
                "add_patient.html",
                error="Le nom et le prénom sont obligatoires.",
                last_name=last_name,
                first_name=first_name,
                date_of_birth=date_of_birth or "",
                date_day=date_day,
                date_month=date_month,
                date_year=date_year,
                profession=request.form.get("profession", "").strip(),
                phone=request.form.get("phone", "").strip(),
                other_phone_1=request.form.get("other_phone_1", "").strip(),
                other_phone_2=request.form.get("other_phone_2", "").strip(),
                address=request.form.get("address", "").strip(),
                insurance=request.form.get("insurance", "").strip(),
                matricule=request.form.get("matricule", "").strip(),
                identifiant_1=request.form.get("identifiant_1", "").strip(),
                identifiant_2=request.form.get("identifiant_2", "").strip(),
                identifiant_final=request.form.get("identifiant_final", "").strip(),
            )
        row = {
            "last_name": last_name,
            "first_name": first_name,
            "date_of_birth": date_of_birth,
            "profession": (request.form.get("profession") or "").strip() or None,
            "phone": (request.form.get("phone") or "").strip() or None,
            "other_phone_1": (request.form.get("other_phone_1") or "").strip() or None,
            "other_phone_2": (request.form.get("other_phone_2") or "").strip() or None,
            "address": (request.form.get("address") or "").strip() or None,
            "insurance": (request.form.get("insurance") or "").strip() or None,
            "matricule": (request.form.get("matricule") or "").strip() or None,
            "identifiant_1": (request.form.get("identifiant_1") or "").strip() or None,
            "identifiant_2": (request.form.get("identifiant_2") or "").strip() or None,
            "identifiant_final": (request.form.get("identifiant_final") or "").strip() or None,
        }
        for i in range(1, 11):
            v = (request.form.get(f"fiche_{i}") or "").strip() or None
            row[f"fiche_{i}"] = v
        md = get_metadata()
        pt = md.tables["patients"]
        patient_columns = {c.name for c in pt.c if c.name not in ("id", "created_at")}
        row = {k: v for k, v in row.items() if k in patient_columns}
        with get_engine().begin() as con:
            r = con.execute(pt.insert().returning(pt.c.id), row)
            patient_id = r.scalar_one()
        return redirect(url_for("patient_detail", patient_id=patient_id))

    @app.get("/api/patients")
    def api_patients():
        last_name = (request.args.get("last_name") or "").strip()
        first_name = (request.args.get("first_name") or "").strip()
        matricule = (request.args.get("matricule") or "").strip()
        date_of_birth = _normalize_date_input(request.args.get("date_of_birth") or "")
        phone = (request.args.get("phone") or "").strip()
        try:
            limit = int(request.args.get("limit") or "50")
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 200))

        with get_engine().connect() as con:
            base_sql = """
                SELECT
                    id,
                    last_name,
                    first_name,
                    date_of_birth,
                    phone,
                    matricule,
                    identifiant_final
                FROM patients
                ORDER BY id DESC
                LIMIT :limit
            """.strip()

            where_parts: list[str] = []
            params: dict[str, Any] = {"limit": limit}

            if last_name:
                params["last_name_like"] = f"%{last_name}%"
                where_parts.append("lower(last_name) LIKE lower(:last_name_like)")
            if first_name:
                params["first_name_like"] = f"%{first_name}%"
                where_parts.append("lower(first_name) LIKE lower(:first_name_like)")
            if matricule:
                params["matricule_like"] = f"%{matricule}%"
                where_parts.append("matricule LIKE :matricule_like")
            if phone:
                params["phone_like"] = f"%{phone}%"
                where_parts.append(
                    "(phone LIKE :phone_like OR other_phone_1 LIKE :phone_like OR other_phone_2 LIKE :phone_like)"
                )
            if date_of_birth:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_of_birth):
                    params["dob_eq"] = date_of_birth
                    where_parts.append("date_of_birth = :dob_eq")
                else:
                    params["dob_like"] = f"%{date_of_birth}%"
                    where_parts.append("date_of_birth LIKE :dob_like")

            if where_parts:
                sql_used = base_sql.replace(
                    "FROM patients",
                    "FROM patients\nWHERE " + "\n  AND ".join(where_parts),
                )
            else:
                sql_used = base_sql

            res = con.execute(text(sql_used), params)
            rows = [dict(r) for r in res.mappings().all()]
        return jsonify({"count": len(rows), "rows": rows})

    @app.post("/reset-demo-data")
    def reset_demo_data():
        init_db(force_reset=True)
        return redirect(url_for("index"))

    return app


def get_database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        # Railway, Render, etc. fournissent souvent postgresql:// ; SQLAlchemy + psycopg a besoin de postgresql+psycopg://
        if url.startswith("postgresql://") and "postgresql+psycopg" not in url:
            url = "postgresql+psycopg://" + url.split("://", 1)[1]
        return url
    # SQLite local par défaut (dev)
    return f"sqlite+pysqlite:///{DB_PATH.resolve().as_posix()}"


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(get_database_url(), pool_pre_ping=True)
    return _ENGINE


def get_metadata() -> MetaData:
    md = MetaData()
    patients = Table(
        "patients",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("last_name", String(200), nullable=False),
        Column("first_name", String(200), nullable=False),
        Column("date_of_birth", String(50)),
        Column("profession", String(200)),
        Column("phone", String(50)),
        Column("other_phone_1", String(50)),
        Column("other_phone_2", String(50)),
        Column("address", String(500)),
        Column("insurance", String(200)),
        Column("matricule", String(100)),
        Column("fiche_1", String(200)),
        Column("fiche_2", String(200)),
        Column("fiche_3", String(200)),
        Column("fiche_4", String(200)),
        Column("fiche_5", String(200)),
        Column("fiche_6", String(200)),
        Column("fiche_7", String(200)),
        Column("fiche_8", String(200)),
        Column("fiche_9", String(200)),
        Column("fiche_10", String(200)),
        Column("identifiant_1", String(200)),
        Column("identifiant_2", String(200)),
        Column("identifiant_final", String(200)),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    consultations = Table(
        "consultations",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "patient_id",
            Integer,
            ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("consultation_date", String(50), nullable=False),
        Column("consultation_detail", String(2000)),
        Column("montant_acte", Float),
        Column("montant_recu", Float),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    Index("idx_patients_name", patients.c.last_name, patients.c.first_name)
    Index("idx_patients_phone", patients.c.phone)
    Index("idx_patients_matricule", patients.c.matricule)
    Index("idx_patients_ident_final", patients.c.identifiant_final)
    Index("idx_consultations_patient_date", consultations.c.patient_id, consultations.c.consultation_date)

    return md


def init_db(force_reset: bool = False) -> None:
    engine = get_engine()
    md = get_metadata()
    if force_reset:
        md.drop_all(engine, checkfirst=True)
    md.create_all(engine, checkfirst=True)

    with engine.begin() as con:
        count = int(con.execute(text("SELECT COUNT(*) FROM patients")).scalar_one())
        if count == 0:
            con.execute(
                md.tables["patients"].insert(),
                [
                    {
                        "last_name": "Dupont",
                        "first_name": "Claire",
                        "date_of_birth": "1991-05-14",
                        "profession": "Infirmière",
                        "phone": "0600000001",
                        "other_phone_1": "0700000001",
                        "other_phone_2": None,
                        "address": "12 rue des Lilas, 75001 Paris",
                        "insurance": "CNAM",
                        "matricule": "MAT-001",
                        "fiche_1": "FICHE-A1",
                        "fiche_2": "FICHE-A2",
                        "identifiant_1": "ID1-CLD",
                        "identifiant_2": "ID2-CLD",
                        "identifiant_final": "FINAL-CLD-001",
                    },
                    {
                        "last_name": "Traoré",
                        "first_name": "Moussa",
                        "date_of_birth": "1987-11-02",
                        "profession": "Chauffeur",
                        "phone": "0600000002",
                        "other_phone_1": None,
                        "other_phone_2": None,
                        "address": "3 avenue Victor Hugo, 69000 Lyon",
                        "insurance": "Privée",
                        "matricule": "MAT-002",
                        "fiche_1": "FICHE-B1",
                        "fiche_2": None,
                        "identifiant_1": "ID1-MTR",
                        "identifiant_2": None,
                        "identifiant_final": "FINAL-MTR-002",
                    },
                ],
            )
            con.execute(
                md.tables["consultations"].insert(),
                [
                    {
                        "patient_id": 1,
                        "consultation_date": "2026-01-10",
                        "consultation_detail": "Consultation générale",
                        "montant_acte": 50.0,
                        "montant_recu": 50.0,
                    },
                    {
                        "patient_id": 1,
                        "consultation_date": "2026-01-25",
                        "consultation_detail": "Contrôle",
                        "montant_acte": 30.0,
                        "montant_recu": 30.0,
                    },
                    {
                        "patient_id": 2,
                        "consultation_date": "2026-01-20",
                        "consultation_detail": "Douleur lombaire",
                        "montant_acte": 60.0,
                        "montant_recu": 40.0,
                    },
                ],
            )


_DISALLOWED_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|vacuum)\b",
    re.IGNORECASE,
)


def run_readonly_sql(sql_text: str) -> tuple[list[str], list[list[Any]]]:
    if not sql_text:
        raise ValueError("La requête est vide.")

    # Une seule instruction, pas de ';'
    if ";" in sql_text:
        raise ValueError("Merci de fournir une seule requête (sans point-virgule ';').")

    if _DISALLOWED_SQL.search(sql_text):
        raise ValueError("Seules les requêtes en lecture (SELECT/WITH) sont autorisées.")

    lowered = sql_text.lstrip().lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("Seules les requêtes SELECT (ou WITH ... SELECT) sont autorisées.")

    with get_engine().connect() as con:
        res = con.execute(text(sql_text))
        rows = res.fetchmany(200)
        cols = list(res.keys())
        data = [list(r) for r in rows]
        return cols, data


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(host=host, port=port, debug=not os.environ.get("PORT"))
