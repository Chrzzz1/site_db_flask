from __future__ import annotations

import json
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import (
    Boolean,
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
from werkzeug.security import check_password_hash, generate_password_hash


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


def _normalize_fiche_url(url: str) -> str:
    """Préfixe https:// si l’URL de fiche n’a pas de schéma (évite lien relatif → 404)."""
    if not url or not isinstance(url, str):
        return url or ""
    u = url.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return "https://" + u


def _parse_date_parts(date_str: str) -> tuple[str, str, str]:
    """Extrait (jour, mois, année) depuis YYYY-MM-DD pour pré-remplir les selects."""
    if not date_str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str.strip()):
        return ("", "", "")
    y, m, d = date_str.strip().split("-")
    return (d.lstrip("0") or "1", m.lstrip("0") or "1", y)


def _send_confirmation_email(to_email: str, confirm_url: str) -> bool:
    """Envoie l'email de confirmation d'inscription. Retourne True si envoyé, False sinon."""
    server = os.environ.get("MAIL_SERVER", "").strip()
    if not server:
        return False
    port = int(os.environ.get("MAIL_PORT", "587"))
    use_tls = os.environ.get("MAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
    username = os.environ.get("MAIL_USERNAME", "").strip()
    password = os.environ.get("MAIL_PASSWORD", "")
    from_addr = os.environ.get("MAIL_FROM", username or "noreply@example.com").strip()
    subject = "Confirmez votre demande d'accès"
    body_text = (
        "Bonjour,\n\n"
        "Vous avez demandé un accès à l'application.\n\n"
        "Cliquez sur le lien suivant pour confirmer votre inscription "
        "(ce lien est valable 24 heures) :\n\n"
        f"{confirm_url}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.\n"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    try:
        with smtplib.SMTP(server, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.sendmail(from_addr, [to_email], msg.as_string())
        return True
    except Exception:
        return False


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

    @app.template_filter("dateonly")
    def _dateonly_filter(val: Any) -> str:
        """Affiche une date sans l'heure (ou — si vide)."""
        if val is None:
            return "—"
        if hasattr(val, "strftime"):
            return val.strftime("%Y-%m-%d")
        s = str(val).strip()
        if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
            return s[:10]
        return s or "—"

    # Créer data/ seulement en local (SQLite) ; en production DATABASE_URL pointe vers PostgreSQL
    if not os.environ.get("DATABASE_URL", "").strip():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    def login_required(f):
        from functools import wraps
        @wraps(f)
        def _inner(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login", next=request.url))
            return f(*args, **kwargs)
        return _inner

    def admin_required(f):
        from functools import wraps
        @wraps(f)
        def _inner(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login", next=request.url))
            if not session.get("is_admin"):
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return _inner

    @app.get("/login")
    def login():
        if session.get("user_id"):
            return redirect(url_for("index"))
        return render_template("login.html", error="")

    @app.post("/login")
    def login_post():
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = request.form.get("next") or request.args.get("next") or url_for("index")
        if not (next_url.startswith("/") and "//" not in next_url[1:]):
            next_url = url_for("index")
        if not username or not password:
            return render_template("login.html", error="Identifiant et mot de passe requis.")
        md = get_metadata()
        users_t = md.tables["users"]
        with get_engine().connect() as con:
            row = con.execute(
                text("SELECT id, password_hash, is_admin, is_approved FROM users WHERE username = :u"),
                {"u": username},
            ).mappings().first()
        if not row:
            return render_template("login.html", error="Identifiant ou mot de passe incorrect.")
        if not check_password_hash(row["password_hash"], password):
            return render_template("login.html", error="Identifiant ou mot de passe incorrect.")
        if not row["is_approved"]:
            return render_template(
                "login.html",
                error="Votre compte n'a pas encore été accepté par un administrateur.",
            )
        session.clear()
        session["user_id"] = row["id"]
        session["username"] = username
        session["is_admin"] = bool(row["is_admin"])
        return redirect(next_url)

    @app.get("/register")
    def register():
        if session.get("user_id"):
            return redirect(url_for("index"))
        if session.get("is_admin"):
            return redirect(url_for("admin_data"))
        return render_template("register.html", error="", request_account=True)

    @app.post("/register")
    def register_post():
        from urllib.parse import quote
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        is_admin_form = request.form.get("is_admin") == "on"
        engine = get_engine()
        md = get_metadata()
        users_t = md.tables["users"]

        if session.get("is_admin"):
            # Création de compte par l'admin : compte approuvé d'office
            def admin_redirect(err: str):
                return redirect(url_for("admin_create_account") + "?create_error=" + quote(err))
            if not username or not password:
                return admin_redirect("Identifiant et mot de passe requis.")
            if len(username) < 2:
                return admin_redirect("L'identifiant doit faire au moins 2 caractères.")
            if len(password) < 6:
                return admin_redirect("Le mot de passe doit faire au moins 6 caractères.")
            if password != password2:
                return admin_redirect("Les deux mots de passe ne correspondent pas.")
            try:
                with engine.connect() as con:
                    exists = con.execute(text("SELECT 1 FROM users WHERE username = :u"), {"u": username}).scalar()
                    if exists:
                        return admin_redirect("Cet identifiant est déjà pris.")
                with engine.begin() as con:
                    # INSERT explicite pour éviter soucis de typage SQLite/PostgreSQL
                    con.execute(
                        text(
                            "INSERT INTO users (username, password_hash, is_admin, is_approved) "
                            "VALUES (:u, :p, :is_admin, :is_approved)"
                        ),
                        {
                            "u": username,
                            "p": generate_password_hash(password),
                            "is_admin": is_admin_form,
                            "is_approved": True,
                        },
                    )
                return redirect(url_for("admin_create_account") + "?created=1")
            except Exception as e:
                return admin_redirect(f"Erreur lors de la création du compte : {str(e)}")

        # Inscription publique : envoi d'un email de confirmation, puis création du compte au clic sur le lien
        email = (request.form.get("email") or "").strip().lower()
        if not username or not password:
            return render_template("register.html", error="Identifiant, email et mot de passe requis.", request_account=True)
        if len(username) < 2:
            return render_template("register.html", error="L'identifiant doit faire au moins 2 caractères.", request_account=True)
        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
            return render_template("register.html", error="Adresse email invalide.", request_account=True)
        if len(password) < 6:
            return render_template("register.html", error="Le mot de passe doit faire au moins 6 caractères.", request_account=True)
        if password != password2:
            return render_template("register.html", error="Les deux mots de passe ne correspondent pas.", request_account=True)
        with engine.connect() as con:
            exists = con.execute(text("SELECT 1 FROM users WHERE username = :u"), {"u": username}).scalar()
            if exists:
                return render_template("register.html", error="Cet identifiant est déjà pris.", request_account=True)
            exists_pending = con.execute(
                text("SELECT 1 FROM pending_confirmations WHERE username = :u OR email = :e"),
                {"u": username, "e": email},
            ).scalar()
            if exists_pending:
                return render_template(
                    "register.html",
                    error="Une demande est déjà en attente pour cet identifiant ou cette adresse email. Vérifiez vos emails ou réessayez plus tard.",
                    request_account=True,
                )
            count = con.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        pending_t = md.tables["pending_confirmations"]
        with engine.begin() as con:
            con.execute(
                pending_t.insert(),
                {
                    "email": email,
                    "username": username,
                    "password_hash": generate_password_hash(password),
                    "token": token,
                    "expires_at": expires_at,
                },
            )
        confirm_url = request.url_root.rstrip("/") + url_for("confirm_email", token=token)
        email_sent = _send_confirmation_email(email, confirm_url)
        if email_sent:
            return redirect(url_for("confirm_email_sent") + "?email=" + quote(email, safe=""))
        # Mode dev : pas d'email configuré, afficher le lien sur une page
        return redirect(url_for("confirm_email_sent", token=token, dev="1"))

    @app.get("/register/confirm-sent")
    def confirm_email_sent():
        """Page après envoi de la demande : email envoyé ou (en dev) lien de confirmation affiché."""
        email = request.args.get("email", "")
        token = request.args.get("token", "")
        dev = request.args.get("dev") == "1"
        return render_template(
            "confirm_email_sent.html",
            email=email,
            token=token,
            dev=dev,
            confirm_url=url_for("confirm_email", token=token) if token else "",
        )

    @app.get("/confirm-email")
    def confirm_email():
        """Confirmation d'inscription via le lien reçu par email."""
        token = (request.args.get("token") or "").strip()
        if not token:
            return render_template("confirm_email_result.html", success=False, error="Lien invalide.")
        engine = get_engine()
        md = get_metadata()
        users_t = md.tables["users"]
        pending_t = md.tables["pending_confirmations"]
        with engine.connect() as con:
            row = con.execute(
                text(
                    "SELECT id, email, username, password_hash FROM pending_confirmations "
                    "WHERE token = :t AND expires_at > :now"
                ),
                {"t": token, "now": datetime.now(timezone.utc)},
            ).mappings().first()
        if not row:
            with engine.connect() as con:
                exists = con.execute(
                    text("SELECT 1 FROM pending_confirmations WHERE token = :t"),
                    {"t": token},
                ).scalar()
            if exists:
                return render_template("confirm_email_result.html", success=False, error="Ce lien a expiré.")
            return render_template("confirm_email_result.html", success=False, error="Lien invalide.")
        with engine.connect() as con:
            user_exists = con.execute(text("SELECT 1 FROM users WHERE username = :u"), {"u": row["username"]}).scalar()
        if user_exists:
            with engine.begin() as con:
                con.execute(text("DELETE FROM pending_confirmations WHERE token = :t"), {"t": token})
            return render_template("confirm_email_result.html", success=False, error="Ce compte existe déjà.")
        count = 0
        with engine.connect() as con:
            count = con.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        is_first = count == 0
        with engine.begin() as con:
            con.execute(
                users_t.insert(),
                {
                    "username": row["username"],
                    "password_hash": row["password_hash"],
                    "is_admin": is_first,
                    "is_approved": is_first,
                },
            )
            con.execute(text("DELETE FROM pending_confirmations WHERE token = :t"), {"t": token})
        if is_first:
            return redirect(url_for("login") + "?msg=premier-compte-admin")
        return redirect(url_for("login") + "?msg=compte-en-attente")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index() -> str:
        q = (request.args.get("q") or "").strip()
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

        if q:
            params["q_like"] = f"%{q}%"
            where_parts.append(
                "(lower(p.last_name) LIKE lower(:q_like) OR lower(p.first_name) LIKE lower(:q_like) "
                "OR lower(p.last_name || ' ' || p.first_name) LIKE lower(:q_like))"
            )
        else:
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
            for r in rows:
                r["is_pending"] = False
            # Fusion avec les patients en attente (table de transition) du même utilisateur
            pending_sql = """
                SELECT
                    pp.id,
                    pp.last_name,
                    pp.first_name,
                    pp.date_of_birth,
                    pp.phone,
                    pp.matricule,
                    (SELECT pc.consultation_date FROM pending_consultations pc
                     WHERE pc.pending_patient_id = pp.id ORDER BY pc.consultation_date DESC, pc.id DESC LIMIT 1) AS last_consultation_date
                FROM pending_patients pp
                WHERE pp.user_id = :uid AND pp.status = 'pending'
            """
            pending_where: list[str] = []
            pending_params: dict[str, Any] = {"uid": session.get("user_id") or 0, "limit": limit}
            if q:
                pending_params["q_like"] = f"%{q}%"
                pending_where.append(
                    "(lower(pp.last_name) LIKE lower(:q_like) OR lower(pp.first_name) LIKE lower(:q_like) "
                    "OR lower(pp.last_name || ' ' || pp.first_name) LIKE lower(:q_like))"
                )
            else:
                if last_name:
                    pending_params["last_name_like"] = f"%{last_name}%"
                    pending_where.append("lower(pp.last_name) LIKE lower(:last_name_like)")
                if first_name:
                    pending_params["first_name_like"] = f"%{first_name}%"
                    pending_where.append("lower(pp.first_name) LIKE lower(:first_name_like)")
            if matricule:
                pending_params["matricule_like"] = f"%{matricule}%"
                pending_where.append("pp.matricule LIKE :matricule_like")
            if phone:
                pending_params["phone_like"] = f"%{phone}%"
                pending_where.append("(pp.phone LIKE :phone_like OR pp.other_phone_1 LIKE :phone_like OR pp.other_phone_2 LIKE :phone_like)")
            if date_of_birth:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_of_birth):
                    pending_params["dob_eq"] = date_of_birth
                    pending_params["dob_like_start"] = date_of_birth + "%"
                    y, m, d = date_of_birth.split("-")
                    pending_params["dob_ddmmyyyy"] = f"{d}/{m}/{y}"
                    pending_where.append("(TRIM(pp.date_of_birth) = :dob_eq OR pp.date_of_birth LIKE :dob_like_start OR pp.date_of_birth = :dob_ddmmyyyy)")
                elif "%" in date_of_birth:
                    pending_params["dob_like"] = date_of_birth
                    pending_where.append("pp.date_of_birth LIKE :dob_like")
                else:
                    pending_params["dob_like"] = f"%{date_of_birth}%"
                    pending_where.append("pp.date_of_birth LIKE :dob_like")
            if pending_where:
                pending_sql += "\n  AND " + "\n  AND ".join(pending_where)
            pending_sql += "\nORDER BY pp.id DESC LIMIT :limit"
            with get_engine().connect() as con:
                pending_res = con.execute(text(pending_sql), pending_params)
                pending_rows = [dict(r) for r in pending_res.mappings().all()]
            for r in pending_rows:
                r["is_pending"] = True
            rows = rows + pending_rows
        else:
            sql_used = ""
            params_used = []
            rows = []

        return render_template(
            "index.html",
            q=q,
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

    # ----- Rendez-vous (appointments) -----
    @app.get("/appointments")
    @app.get("/appointments/calendar")
    @login_required
    def appointments_calendar():
        """Calendrier type Google Agenda pour visualiser les rendez-vous."""
        from datetime import date, timedelta
        today = date.today()
        try:
            year = int(request.args.get("year") or today.year)
            month = int(request.args.get("month") or today.month)
        except ValueError:
            year, month = today.year, today.month
        # Limiter la plage
        year = max(2020, min(2030, year))
        month = max(1, min(12, month))
        first = date(year, month, 1)
        if month == 12:
            last = date(year, 12, 31)
        else:
            last = date(year, month + 1, 1) - timedelta(days=1)
        start_str = first.strftime("%Y-%m-%d")
        end_str = (last + timedelta(days=1)).strftime("%Y-%m-%d")
        with get_engine().connect() as con:
            rows = con.execute(
                text(
                    """
                    SELECT a.id, a.patient_id, a.appointment_date, a.appointment_time, a.notes, p.last_name, p.first_name
                    FROM appointments a
                    JOIN patients p ON p.id = a.patient_id
                    WHERE a.appointment_date >= :start AND a.appointment_date < :end
                    ORDER BY a.appointment_date, a.appointment_time
                    """
                ),
                {"start": start_str, "end": end_str},
            ).mappings().all()
        appointments_by_date: dict[str, list] = {}
        for r in rows:
            d = r["appointment_date"]
            if d not in appointments_by_date:
                appointments_by_date[d] = []
            appointments_by_date[d].append(dict(r))
        # Grille du mois (lun=0 ... dim=6)
        calendar_weeks = []
        week = [None] * 7
        for d in range(1, last.day + 1):
            dt = date(year, month, d)
            idx = dt.weekday()  # 0=lundi, 6=dimanche
            week[idx] = dt
            if idx == 6 or d == last.day:
                calendar_weeks.append(week)
                week = [None] * 7
        if any(week):
            calendar_weeks.append(week)
        month_names = ("", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                      "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre")
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        return render_template(
            "appointments_calendar.html",
            year=year,
            month=month,
            month_name=month_names[month],
            calendar_weeks=calendar_weeks,
            appointments_by_date=appointments_by_date,
            today_str=today.strftime("%Y-%m-%d"),
            prev_url=url_for("appointments_calendar", year=prev_year, month=prev_month),
            next_url=url_for("appointments_calendar", year=next_year, month=next_month),
        )

    @app.get("/appointments/day/<date_str>")
    @login_required
    def appointments_day_detail(date_str: str):
        """Rendez-vous d'un jour donné avec détails (heure, patient, notes)."""
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            return redirect(url_for("appointments_calendar"))
        with get_engine().connect() as con:
            rows = con.execute(
                text(
                    """
                    SELECT a.id, a.patient_id, a.appointment_date, a.appointment_time, a.notes, p.last_name, p.first_name
                    FROM appointments a
                    JOIN patients p ON p.id = a.patient_id
                    WHERE a.appointment_date = :d
                    ORDER BY COALESCE(a.appointment_time, 'zzz'), a.id
                    """
                ),
                {"d": date_str},
            ).mappings().all()
        appointments = [dict(r) for r in rows]
        month_names = ("", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                      "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre")
        try:
            y, m, d = map(int, date_str.split("-"))
            day_label = f"{d} {month_names[m]} {y}"
        except (ValueError, IndexError):
            day_label = date_str
        return render_template(
            "appointments_day.html",
            date_str=date_str,
            day_label=day_label,
            appointments=appointments,
        )

    @app.get("/appointments/list")
    @login_required
    def appointments_list():
        period = (request.args.get("period") or "week").strip()
        today = datetime.now().date()
        if period == "month":
            from datetime import date as d
            end = d(today.year, today.month + 1, 1) if today.month < 12 else d(today.year + 1, 1, 1)
            end_str = end.strftime("%Y-%m-%d")
        else:
            from datetime import timedelta
            end_date = today + timedelta(days=7)
            end_str = end_date.strftime("%Y-%m-%d")
        start_str = today.strftime("%Y-%m-%d")
        with get_engine().connect() as con:
            rows = con.execute(
                text(
                    """
                    SELECT a.id, a.patient_id, a.appointment_date, a.appointment_time, a.notes, p.last_name, p.first_name
                    FROM appointments a
                    JOIN patients p ON p.id = a.patient_id
                    WHERE a.appointment_date >= :start AND a.appointment_date < :end
                    ORDER BY a.appointment_date, a.appointment_time
                    """
                ),
                {"start": start_str, "end": end_str},
            ).mappings().all()
        return render_template(
            "appointments.html",
            appointments=[dict(r) for r in rows],
            period=period,
        )

    @app.get("/appointments/new")
    @login_required
    def appointment_new_form():
        patient_id = request.args.get("patient_id", type=int)
        preselected_date = request.args.get("appointment_date", "").strip() or None
        preselected_name = ""
        if patient_id:
            with get_engine().connect() as con:
                row = con.execute(
                    text("SELECT last_name, first_name FROM patients WHERE id = :id"),
                    {"id": patient_id},
                ).mappings().first()
                if row:
                    preselected_name = f"{row['last_name']} {row['first_name']}"
        return render_template(
            "appointment_new.html",
            preselected_patient_id=patient_id,
            preselected_name=preselected_name,
            preselected_date=preselected_date,
            error="",
        )

    @app.post("/appointments/new")
    @login_required
    def appointment_new_submit():
        patient_id = request.form.get("patient_id", type=int)
        appointment_date = (request.form.get("appointment_date") or "").strip()
        appointment_time = (request.form.get("appointment_time") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None
        if not patient_id or not appointment_date:
            preselected_name = ""
            if patient_id:
                with get_engine().connect() as con:
                    row = con.execute(
                        text("SELECT last_name, first_name FROM patients WHERE id = :id"),
                        {"id": patient_id},
                    ).mappings().first()
                    if row:
                        preselected_name = f"{row['last_name']} {row['first_name']}"
            return render_template(
                "appointment_new.html",
                preselected_patient_id=patient_id,
                preselected_name=preselected_name,
                preselected_date=appointment_date or None,
                error="Patient et date obligatoires.",
            )
        with get_engine().connect() as con:
            exists = con.execute(text("SELECT 1 FROM patients WHERE id = :id"), {"id": patient_id}).first()
        if not exists:
            return redirect(url_for("appointments_calendar"))
        with get_engine().begin() as con:
            con.execute(
                text(
                    "INSERT INTO appointments (patient_id, appointment_date, appointment_time, notes) "
                    "VALUES (:pid, :d, :t, :n)"
                ),
                {"pid": patient_id, "d": appointment_date, "t": appointment_time, "n": notes},
            )
        _log_action(
            session.get("user_id"),
            session.get("username") or "?",
            "appointment_created",
            "appointment",
            None,
            f"Rendez-vous {appointment_date}",
        )
        return redirect(url_for("appointments_calendar", year=datetime.now().year, month=datetime.now().month) + "?msg=rdv-ajoute")

    @app.get("/patients/<int:patient_id>")
    @login_required
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

            appointments_rows = con.execute(
                text(
                    """
                    SELECT id, appointment_date, appointment_time, notes
                    FROM appointments
                    WHERE patient_id = :patient_id
                    ORDER BY appointment_date DESC, COALESCE(appointment_time, 'zzz') DESC
                    LIMIT 100
                    """
                ),
                {"patient_id": patient_id},
            ).mappings().all()

        today_str = datetime.now().strftime("%Y-%m-%d")
        appointments_past = []
        appointments_future = []
        for r in appointments_rows if patient else []:
            d = r["appointment_date"]
            row = dict(r)
            if d < today_str:
                appointments_past.append(row)
            else:
                appointments_future.append(row)
        appointments_future.reverse()

        if patient is None:
            return render_template(
                "patient.html",
                patient=None,
                consultations=[],
                fiches=[],
                error=error,
            )

        fiches = [
            (i, _normalize_fiche_url(patient[f"fiche_{i}"]))
            for i in range(1, 11)
            if patient[f"fiche_{i}"]
        ]
        return render_template(
            "patient.html",
            patient=patient,
            consultations=consultations,
            fiches=fiches,
            appointments_past=appointments_past,
            appointments_future=appointments_future,
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
        _log_action(
            session.get("user_id"),
            session.get("username") or "?",
            "consultation_created",
            "consultation",
            None,
            f"Patient #{patient_id}, date {consultation_date}",
        )
        return redirect(url_for("patient_detail", patient_id=patient_id))

    # ----- Patients en attente (table de transition) -----
    @app.get("/pending-patients/<int:pending_id>")
    @login_required
    def pending_patient_detail(pending_id: int):
        error = (request.args.get("error") or "").strip()
        with get_engine().connect() as con:
            row = con.execute(
                text(
                    "SELECT id, user_id, status, last_name, first_name, date_of_birth, profession, phone, "
                    "other_phone_1, other_phone_2, address, insurance, matricule, "
                    "fiche_1, fiche_2, fiche_3, fiche_4, fiche_5, fiche_6, fiche_7, fiche_8, fiche_9, fiche_10, created_at "
                    "FROM pending_patients WHERE id = :id"
                ),
                {"id": pending_id},
            ).mappings().first()
        if not row or row["user_id"] != session.get("user_id") or row["status"] != "pending":
            return redirect(url_for("index"))
        patient = dict(row)
        with get_engine().connect() as con:
            consultations = con.execute(
                text(
                    "SELECT id, consultation_date, consultation_detail, montant_acte, montant_recu "
                    "FROM pending_consultations WHERE pending_patient_id = :pid ORDER BY consultation_date DESC, id DESC LIMIT 200"
                ),
                {"pid": pending_id},
            ).mappings().all()
        fiches = [
            (i, _normalize_fiche_url(patient.get(f"fiche_{i}")))
            for i in range(1, 11)
            if patient.get(f"fiche_{i}")
        ]
        return render_template(
            "patient.html",
            patient=patient,
            consultations=list(consultations),
            fiches=fiches,
            appointments_past=[],
            appointments_future=[],
            error=error,
            is_pending=True,
            pending_id=pending_id,
        )

    @app.post("/pending-patients/<int:pending_id>/consultations")
    @login_required
    def add_pending_consultation(pending_id: int):
        with get_engine().connect() as con:
            row = con.execute(
                text("SELECT id, user_id, status FROM pending_patients WHERE id = :id"),
                {"id": pending_id},
            ).mappings().first()
        if not row or row["user_id"] != session.get("user_id") or row["status"] != "pending":
            return redirect(url_for("index"))
        consultation_date = (request.form.get("consultation_date") or "").strip()
        consultation_detail = (request.form.get("consultation_detail") or "").strip() or None
        montant_acte_raw = (request.form.get("montant_acte") or "").strip()
        montant_recu_raw = (request.form.get("montant_recu") or "").strip()
        if not consultation_date:
            return redirect(url_for("pending_patient_detail", pending_id=pending_id, error="La date de consultation est obligatoire."))
        def _parse(v: str) -> float | None:
            if not v:
                return None
            return float(v.replace(",", "."))
        try:
            montant_acte, montant_recu = _parse(montant_acte_raw), _parse(montant_recu_raw)
        except ValueError:
            return redirect(url_for("pending_patient_detail", pending_id=pending_id, error="Montant invalide."))
        with get_engine().begin() as con:
            con.execute(
                text(
                    "INSERT INTO pending_consultations (pending_patient_id, consultation_date, consultation_detail, montant_acte, montant_recu) "
                    "VALUES (:pid, :d, :det, :ma, :mr)"
                ),
                {"pid": pending_id, "d": consultation_date, "det": consultation_detail, "ma": montant_acte, "mr": montant_recu},
            )
        return redirect(url_for("pending_patient_detail", pending_id=pending_id))

    @app.get("/pending-patients/<int:pending_id>/edit")
    @login_required
    def pending_patient_edit_form(pending_id: int):
        with get_engine().connect() as con:
            row = con.execute(
                text(
                    "SELECT id, last_name, first_name, date_of_birth, profession, phone, other_phone_1, other_phone_2, "
                    "address, insurance, matricule, fiche_1, fiche_2, fiche_3, fiche_4, fiche_5, fiche_6, fiche_7, "
                    "fiche_8, fiche_9, fiche_10 FROM pending_patients WHERE id = :id AND user_id = :uid AND status = 'pending'"
                ),
                {"id": pending_id, "uid": session.get("user_id")},
            ).mappings().first()
        if not row:
            return redirect(url_for("index"))
        patient = dict(row)
        date_day, date_month, date_year = _parse_date_parts(patient.get("date_of_birth") or "")
        return render_template(
            "edit_patient.html",
            patient=patient,
            patient_id=pending_id,
            is_admin=False,
            date_day=date_day,
            date_month=date_month,
            date_year=date_year,
            error="",
            is_pending=True,
            pending_id=pending_id,
        )

    @app.post("/pending-patients/<int:pending_id>/edit")
    @login_required
    def pending_patient_edit_submit(pending_id: int):
        with get_engine().connect() as con:
            row = con.execute(
                text("SELECT id, user_id, status FROM pending_patients WHERE id = :id"),
                {"id": pending_id},
            ).mappings().first()
        if not row or row["user_id"] != session.get("user_id") or row["status"] != "pending":
            return redirect(url_for("index"))
        date_day = (request.form.get("date_day") or "").strip()
        date_month = (request.form.get("date_month") or "").strip()
        date_year = (request.form.get("date_year") or "").strip()
        date_of_birth = _date_from_parts(date_day, date_month, date_year) if (date_day and date_month and date_year) else None
        if not date_of_birth and (date_day or date_month or date_year):
            date_of_birth = _normalize_date_input(request.form.get("date_of_birth") or "") or None
        upd = {
            "ln": (request.form.get("last_name") or "").strip(),
            "fn": (request.form.get("first_name") or "").strip(),
            "dob": date_of_birth,
            "prof": (request.form.get("profession") or "").strip() or None,
            "ph": (request.form.get("phone") or "").strip() or None,
            "o1": (request.form.get("other_phone_1") or "").strip() or None,
            "o2": (request.form.get("other_phone_2") or "").strip() or None,
            "addr": (request.form.get("address") or "").strip() or None,
            "ins": (request.form.get("insurance") or "").strip() or None,
            "mat": (request.form.get("matricule") or "").strip() or None,
        }
        for i in range(1, 11):
            upd[f"f{i}"] = (request.form.get(f"fiche_{i}") or "").strip() or None
        if not upd["ln"] or not upd["fn"]:
            return redirect(url_for("pending_patient_edit_form", pending_id=pending_id) + "?error=nom-prenom-requis")
        with get_engine().begin() as con:
            con.execute(
                text(
                    "UPDATE pending_patients SET last_name=:ln, first_name=:fn, date_of_birth=:dob, profession=:prof, "
                    "phone=:ph, other_phone_1=:o1, other_phone_2=:o2, address=:addr, insurance=:ins, matricule=:mat, "
                    "fiche_1=:f1, fiche_2=:f2, fiche_3=:f3, fiche_4=:f4, fiche_5=:f5, fiche_6=:f6, fiche_7=:f7, "
                    "fiche_8=:f8, fiche_9=:f9, fiche_10=:f10 WHERE id=:id"
                ),
                {"id": pending_id, **upd},
            )
        return redirect(url_for("pending_patient_detail", pending_id=pending_id))

    @app.get("/patients/<int:patient_id>/edit")
    @login_required
    def edit_patient_form(patient_id: int):
        with get_engine().connect() as con:
            patient = con.execute(
                text(
                    "SELECT id, last_name, first_name, date_of_birth, profession, phone, other_phone_1, other_phone_2, "
                    "address, insurance, matricule, fiche_1, fiche_2, fiche_3, fiche_4, fiche_5, fiche_6, fiche_7, "
                    "fiche_8, fiche_9, fiche_10 FROM patients WHERE id = :id"
                ),
                {"id": patient_id},
            ).mappings().first()
        if not patient:
            return redirect(url_for("index"))
        patient = dict(patient)
        date_day, date_month, date_year = _parse_date_parts(patient.get("date_of_birth") or "")
        return render_template(
            "edit_patient.html",
            patient=patient,
            patient_id=patient_id,
            is_admin=session.get("is_admin"),
            date_day=date_day,
            date_month=date_month,
            date_year=date_year,
            error="",
        )

    @app.post("/patients/<int:patient_id>/edit")
    @login_required
    def edit_patient_submit(patient_id: int):
        date_day = (request.form.get("date_day") or "").strip()
        date_month = (request.form.get("date_month") or "").strip()
        date_year = (request.form.get("date_year") or "").strip()
        date_of_birth = _date_from_parts(date_day, date_month, date_year) if (date_day and date_month and date_year) else None
        if not date_of_birth and (date_day or date_month or date_year):
            date_of_birth = _normalize_date_input(request.form.get("date_of_birth") or "") or None
        row = {
            "last_name": (request.form.get("last_name") or "").strip(),
            "first_name": (request.form.get("first_name") or "").strip(),
            "date_of_birth": date_of_birth,
            "profession": (request.form.get("profession") or "").strip() or None,
            "phone": (request.form.get("phone") or "").strip() or None,
            "other_phone_1": (request.form.get("other_phone_1") or "").strip() or None,
            "other_phone_2": (request.form.get("other_phone_2") or "").strip() or None,
            "address": (request.form.get("address") or "").strip() or None,
            "insurance": (request.form.get("insurance") or "").strip() or None,
            "matricule": (request.form.get("matricule") or "").strip() or None,
        }
        for i in range(1, 11):
            row[f"fiche_{i}"] = (request.form.get(f"fiche_{i}") or "").strip() or None
        if not row["last_name"] or not row["first_name"]:
            return redirect(url_for("edit_patient_form", patient_id=patient_id) + "?error=nom-prenom-requis")
        if session.get("is_admin"):
            # Récupérer l'état actuel pour l'historique (champs modifiés)
            with get_engine().connect() as con:
                old_row = con.execute(
                    text(
                        "SELECT last_name, first_name, date_of_birth, profession, phone, other_phone_1, other_phone_2, "
                        "address, insurance, matricule, fiche_1, fiche_2, fiche_3, fiche_4, fiche_5, fiche_6, "
                        "fiche_7, fiche_8, fiche_9, fiche_10 FROM patients WHERE id = :id"
                    ),
                    {"id": patient_id},
                ).mappings().first()
            old_data = dict(old_row) if old_row else {}
            changed_labels = _changed_field_labels(old_data, row, PATIENT_FIELD_LABELS)
            details_extra = ""
            if changed_labels:
                details_extra = f". Champs modifiés: {', '.join(changed_labels)}"
            with get_engine().begin() as con:
                con.execute(
                    text(
                        "UPDATE patients SET last_name=:ln, first_name=:fn, date_of_birth=:dob, profession=:prof, "
                        "phone=:ph, other_phone_1=:o1, other_phone_2=:o2, address=:addr, insurance=:ins, matricule=:mat, "
                        "fiche_1=:f1, fiche_2=:f2, fiche_3=:f3, fiche_4=:f4, fiche_5=:f5, fiche_6=:f6, fiche_7=:f7, "
                        "fiche_8=:f8, fiche_9=:f9, fiche_10=:f10 "
                        "WHERE id=:id"
                    ),
                    {
                        "id": patient_id, "ln": row["last_name"], "fn": row["first_name"], "dob": row["date_of_birth"],
                        "prof": row["profession"], "ph": row["phone"], "o1": row["other_phone_1"], "o2": row["other_phone_2"],
                        "addr": row["address"], "ins": row["insurance"], "mat": row["matricule"],
                        "f1": row["fiche_1"], "f2": row["fiche_2"], "f3": row["fiche_3"], "f4": row["fiche_4"],
                        "f5": row["fiche_5"], "f6": row["fiche_6"], "f7": row["fiche_7"], "f8": row["fiche_8"],
                        "f9": row["fiche_9"], "f10": row["fiche_10"],
                    },
                )
            _log_action(
                session.get("user_id"),
                session.get("username") or "?",
                "patient_updated",
                "patient",
                patient_id,
                f"{row['last_name']} {row['first_name']}{details_extra}",
            )
            return redirect(url_for("patient_detail", patient_id=patient_id))
        with get_engine().begin() as con:
            con.execute(
                text(
                    "INSERT INTO modification_requests (request_type, record_id, user_id, proposed_data, status) "
                    "VALUES ('patient', :record_id, :user_id, :data, 'pending')"
                ),
                {
                    "record_id": patient_id,
                    "user_id": session["user_id"],
                    "data": json.dumps(row),
                },
            )
        return redirect(url_for("patient_detail", patient_id=patient_id) + "?msg=modification-en-attente")

    @app.get("/consultations/<int:consultation_id>/edit")
    @login_required
    def edit_consultation_form(consultation_id: int):
        with get_engine().connect() as con:
            c = con.execute(
                text(
                    "SELECT id, patient_id, consultation_date, consultation_detail, montant_acte, montant_recu "
                    "FROM consultations WHERE id = :id"
                ),
                {"id": consultation_id},
            ).mappings().first()
        if not c:
            return redirect(url_for("index"))
        c = dict(c)
        return render_template(
            "edit_consultation.html",
            consultation=c,
            consultation_id=consultation_id,
            patient_id=c["patient_id"],
            is_admin=session.get("is_admin"),
            error="",
        )

    @app.post("/consultations/<int:consultation_id>/edit")
    @login_required
    def edit_consultation_submit(consultation_id: int):
        consultation_date = (request.form.get("consultation_date") or "").strip()
        consultation_detail = (request.form.get("consultation_detail") or "").strip() or None
        montant_acte_raw = (request.form.get("montant_acte") or "").strip()
        montant_recu_raw = (request.form.get("montant_recu") or "").strip()
        def parse_amount(v: str):
            if not v:
                return None
            return float(v.replace(",", "."))
        try:
            montant_acte = parse_amount(montant_acte_raw)
            montant_recu = parse_amount(montant_recu_raw)
        except ValueError:
            montant_acte = montant_recu = None
        if not consultation_date:
            return redirect(url_for("edit_consultation_form", consultation_id=consultation_id) + "?error=date-requise")
        with get_engine().connect() as con:
            c = con.execute(text("SELECT patient_id FROM consultations WHERE id = :id"), {"id": consultation_id}).mappings().first()
            if not c:
                return redirect(url_for("index"))
            patient_id = c["patient_id"]
        row = {
            "consultation_date": consultation_date,
            "consultation_detail": consultation_detail,
            "montant_acte": montant_acte,
            "montant_recu": montant_recu,
        }
        if session.get("is_admin"):
            with get_engine().begin() as con:
                con.execute(
                    text(
                        "UPDATE consultations SET consultation_date=:d, consultation_detail=:det, montant_acte=:ma, montant_recu=:mr WHERE id=:id"
                    ),
                    {"id": consultation_id, "d": consultation_date, "det": consultation_detail, "ma": montant_acte, "mr": montant_recu},
                )
            return redirect(url_for("patient_detail", patient_id=patient_id))
        with get_engine().begin() as con:
            con.execute(
                text(
                    "INSERT INTO modification_requests (request_type, record_id, user_id, proposed_data, status) "
                    "VALUES ('consultation', :record_id, :user_id, :data, 'pending')"
                ),
                {"record_id": consultation_id, "user_id": session["user_id"], "data": json.dumps(row)},
            )
        return redirect(url_for("patient_detail", patient_id=patient_id) + "?msg=modification-en-attente")

    @app.get("/patients/new")
    @login_required
    def add_patient_form() -> str:
        return render_template(
            "add_patient.html",
            error="",
            date_day="",
            date_month="",
            date_year="",
            is_admin=session.get("is_admin"),
        )

    @app.post("/patients/new")
    @login_required
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
                is_admin=session.get("is_admin"),
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
        }
        for i in range(1, 11):
            v = (request.form.get(f"fiche_{i}") or "").strip() or None
            row[f"fiche_{i}"] = v
        md = get_metadata()
        pt = md.tables["patients"]
        patient_columns = {c.name for c in pt.c if c.name not in ("id", "created_at")}
        row_clean = {k: v for k, v in row.items() if k in patient_columns}
        if session.get("is_admin"):
            with get_engine().begin() as con:
                r = con.execute(pt.insert().returning(pt.c.id), row_clean)
                patient_id = r.scalar_one()
            _log_action(
                session.get("user_id"),
                session.get("username") or "?",
                "patient_created",
                "patient",
                patient_id,
                f"{last_name} {first_name}",
            )
            return redirect(url_for("patient_detail", patient_id=patient_id))
        # Non-admin : stocker en table de transition (pending_patients), l'utilisateur peut modifier et ajouter des consultations
        pp = md.tables["pending_patients"]
        pending_cols = {c.name for c in pp.c if c.name not in ("id", "user_id", "status", "created_at", "reviewed_at", "reviewed_by")}
        row_pending = {k: v for k, v in row_clean.items() if k in pending_cols}
        row_pending["user_id"] = session["user_id"]
        row_pending["status"] = "pending"
        with get_engine().begin() as con:
            r = con.execute(pp.insert().returning(pp.c.id), row_pending)
            pending_id = r.scalar_one()
        _log_action(
            session.get("user_id"),
            session.get("username") or "?",
            "new_patient_requested",
            "pending_patient",
            pending_id,
            f"Nouveau patient en attente: {last_name} {first_name}",
        )
        return redirect(url_for("pending_patient_detail", pending_id=pending_id))

    @app.get("/api/patients")
    @login_required
    def api_patients():
        q = (request.args.get("q") or "").strip()
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
                    matricule
                FROM patients
                ORDER BY id DESC
                LIMIT :limit
            """.strip()

            where_parts: list[str] = []
            params: dict[str, Any] = {"limit": limit}

            if q:
                params["q_like"] = f"%{q}%"
                where_parts.append(
                    "(lower(last_name) LIKE lower(:q_like) OR lower(first_name) LIKE lower(:q_like) OR lower(last_name || ' ' || first_name) LIKE lower(:q_like))"
                )
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

    @app.get("/api/appointments")
    @login_required
    def api_appointments():
        """Rendez-vous pour une date ou une plage. Paramètres: date=YYYY-MM-DD ou start= & end="""
        date_val = (request.args.get("date") or "").strip()
        start_val = (request.args.get("start") or "").strip()
        end_val = (request.args.get("end") or "").strip()
        if date_val and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_val):
            start_str = date_val
            end_str = (datetime.strptime(date_val, "%Y-%m-%d").date() + timedelta(days=1)).strftime("%Y-%m-%d")
        elif start_val and end_val and re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_val) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_val):
            start_str, end_str = start_val, end_val
        else:
            return jsonify({"count": 0, "rows": []})
        with get_engine().connect() as con:
            rows = con.execute(
                text(
                    """
                    SELECT a.id, a.patient_id, a.appointment_date, a.appointment_time, a.notes, p.last_name, p.first_name
                    FROM appointments a
                    JOIN patients p ON p.id = a.patient_id
                    WHERE a.appointment_date >= :start AND a.appointment_date < :end
                    ORDER BY a.appointment_date, a.appointment_time
                    """
                ),
                {"start": start_str, "end": end_str},
            ).mappings().all()
        return jsonify({"count": len(rows), "rows": [dict(r) for r in rows]})

    @app.post("/api/upload-fiche")
    @login_required
    def api_upload_fiche():
        """Upload une fiche (PDF/image) vers Google Drive et renvoie l'URL."""
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "Aucun fichier fourni."}), 400
        patient_name = ""
        pid = request.form.get("patient_id", type=int)
        pending_id = request.form.get("pending_id", type=int)
        if pid:
            with get_engine().connect() as con:
                row = con.execute(
                    text("SELECT last_name, first_name FROM patients WHERE id = :id"),
                    {"id": pid},
                ).mappings().first()
            if row:
                patient_name = f"{row['last_name']} {row['first_name']}"
        elif pending_id and session.get("user_id"):
            with get_engine().connect() as con:
                row = con.execute(
                    text("SELECT last_name, first_name FROM pending_patients WHERE id = :id AND user_id = :uid"),
                    {"id": pending_id, "uid": session["user_id"]},
                ).mappings().first()
            if row:
                patient_name = f"{row['last_name']} {row['first_name']}"

        data = file.read()
        mime = (file.content_type or "").strip() or "application/octet-stream"
        if mime == "application/octet-stream" and file.filename.lower().endswith(".pdf"):
            mime = "application/pdf"

        try:
            from drive_upload import upload_fiche_to_drive

            url = upload_fiche_to_drive(data, file.filename, mime, patient_name)
        except ImportError:
            url = None

        if not url:
            return jsonify({"error": "Upload non configuré ou échec. Vérifiez GOOGLE_DRIVE_CREDENTIALS_JSON et GOOGLE_DRIVE_FOLDER_ID."}), 500
        return jsonify({"url": url})

    def _admin_mod_reqs_with_summary(rows):
        mod_reqs = [dict(r) for r in rows]
        for mr in mod_reqs:
            if mr.get("request_type") == "new_patient" and mr.get("proposed_data"):
                try:
                    d = json.loads(mr["proposed_data"])
                    mr["summary"] = f"{d.get('last_name', '')} {d.get('first_name', '')}".strip() or "—"
                except Exception:
                    mr["summary"] = "—"
        return mod_reqs

    @app.get("/admin")
    @admin_required
    def admin():
        with get_engine().connect() as con:
            pending_count = con.execute(text("SELECT COUNT(*) FROM pending_patients WHERE status = 'pending'")).scalar_one()
            modifications_count = con.execute(text("SELECT COUNT(*) FROM modification_requests WHERE status = 'pending'")).scalar_one()
            users_count = con.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        return render_template(
            "admin/index.html",
            pending_count=pending_count,
            modifications_count=modifications_count,
            users_count=users_count,
        )

    @app.get("/admin/pending-patients")
    @admin_required
    def admin_pending_patients():
        with get_engine().connect() as con:
            pending_patients_list = con.execute(
                text(
                    """
                    SELECT pp.id, pp.user_id, pp.last_name, pp.first_name, pp.date_of_birth, pp.phone, pp.matricule, pp.created_at, u.username,
                           (SELECT COUNT(*) FROM pending_consultations pc WHERE pc.pending_patient_id = pp.id) AS consultations_count
                    FROM pending_patients pp
                    JOIN users u ON u.id = pp.user_id
                    WHERE pp.status = 'pending'
                    ORDER BY pp.created_at DESC
                    """
                )
            ).mappings().all()
        return render_template(
            "admin/pending_patients.html",
            pending_patients_list=[dict(r) for r in pending_patients_list],
        )

    @app.get("/admin/modifications")
    @admin_required
    def admin_modifications():
        with get_engine().connect() as con:
            modification_requests = con.execute(
                text(
                    """
                    SELECT mr.id, mr.request_type, mr.record_id, mr.user_id, mr.proposed_data, mr.status, mr.created_at, u.username,
                           CASE WHEN mr.request_type = 'patient' THEN mr.record_id WHEN mr.request_type = 'new_patient' THEN NULL ELSE (SELECT patient_id FROM consultations WHERE id = mr.record_id) END AS patient_id
                    FROM modification_requests mr
                    JOIN users u ON u.id = mr.user_id
                    WHERE mr.status = 'pending'
                    ORDER BY mr.created_at DESC
                    """
                )
            ).mappings().all()
        return render_template(
            "admin/modifications.html",
            modification_requests=_admin_mod_reqs_with_summary(modification_requests),
        )

    @app.get("/admin/users")
    @admin_required
    def admin_users():
        with get_engine().connect() as con:
            users = con.execute(text("SELECT id, username, is_admin, is_approved FROM users ORDER BY id")).mappings().all()
        return render_template(
            "admin/users.html",
            users=[dict(r) for r in users],
            current_user_id=session.get("user_id"),
        )

    @app.get("/admin/create-account")
    @admin_required
    def admin_create_account():
        return render_template("admin/create_account.html")

    @app.get("/admin/history")
    @admin_required
    def admin_history():
        try:
            with get_engine().connect() as con:
                action_log_list = con.execute(
                    text("SELECT id, user_id, username, action, entity_type, entity_id, details, created_at FROM action_log ORDER BY created_at DESC LIMIT 300")
                ).mappings().all()
        except Exception:
            action_log_list = []
        return render_template("admin/history.html", action_log=[dict(r) for r in action_log_list])

    @app.get("/admin/data")
    @admin_required
    def admin_data():
        with get_engine().connect() as con:
            patients = con.execute(
                text("SELECT id, last_name, first_name, date_of_birth, phone, matricule FROM patients ORDER BY id DESC LIMIT 200")
            ).mappings().all()
            consultations = con.execute(
                text(
                    """
                    SELECT c.id, c.patient_id, c.consultation_date, c.consultation_detail, c.montant_acte,
                           p.last_name, p.first_name
                    FROM consultations c
                    JOIN patients p ON p.id = c.patient_id
                    ORDER BY c.id DESC LIMIT 100
                    """
                )
            ).mappings().all()
        return render_template(
            "admin/data.html",
            patients=[dict(r) for r in patients],
            consultations=[dict(r) for r in consultations],
        )

    @app.get("/admin/export-excel", endpoint="admin_export_excel")
    @admin_required
    def admin_export_excel():
        engine = get_engine()
        with engine.connect() as con:
            df_patients = pd.read_sql(text("SELECT * FROM patients ORDER BY id"), con)
            df_consultations = pd.read_sql(text("SELECT * FROM consultations ORDER BY id"), con)
        for df in (df_patients, df_consultations):
            for col in df.columns:
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    df[col] = df[col].astype(str)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_patients.to_excel(writer, sheet_name="Patients", index=False)
            df_consultations.to_excel(writer, sheet_name="Consultations", index=False)
        data = buffer.getvalue()
        filename = f"export_patients_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"
        return Response(
            data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(data)),
            },
        )

    @app.post("/admin/patients/<int:patient_id>/delete")
    @admin_required
    def admin_delete_patient(patient_id: int):
        md = get_metadata()
        with get_engine().begin() as con:
            con.execute(text("DELETE FROM consultations WHERE patient_id = :pid"), {"pid": patient_id})
            con.execute(text("DELETE FROM patients WHERE id = :pid"), {"pid": patient_id})
        return redirect(url_for("admin_data"))

    @app.post("/admin/consultations/<int:consultation_id>/delete")
    @admin_required
    def admin_delete_consultation(consultation_id: int):
        with get_engine().connect() as con:
            c = con.execute(text("SELECT consultation_date, patient_id FROM consultations WHERE id = :id"), {"id": consultation_id}).mappings().first()
        with get_engine().begin() as con:
            con.execute(text("DELETE FROM consultations WHERE id = :cid"), {"cid": consultation_id})
        _log_action(
            session.get("user_id"),
            session.get("username") or "?",
            "consultation_deleted",
            "consultation",
            consultation_id,
            f"Consultation #{consultation_id}" + (f" (patient {c['patient_id']})" if c else ""),
        )
        return redirect(url_for("admin_data"))

    @app.post("/admin/users/<int:user_id>/set-admin")
    @admin_required
    def admin_set_admin(user_id: int):
        with get_engine().begin() as con:
            con.execute(text("UPDATE users SET is_admin = :val WHERE id = :id"), {"val": True, "id": user_id})
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:user_id>/remove-admin")
    @admin_required
    def admin_remove_admin(user_id: int):
        with get_engine().connect() as con:
            count = con.execute(text("SELECT COUNT(*) FROM users WHERE is_admin = :v"), {"v": True}).scalar_one()
        if count <= 1:
            return redirect(url_for("admin_users") + "?error=impossible-retirer-dernier-admin")
        with get_engine().begin() as con:
            con.execute(text("UPDATE users SET is_admin = :val WHERE id = :id"), {"val": False, "id": user_id})
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:user_id>/approve")
    @admin_required
    def admin_approve_user(user_id: int):
        with get_engine().begin() as con:
            con.execute(text("UPDATE users SET is_approved = :ok WHERE id = :id"), {"ok": True, "id": user_id})
        return redirect(url_for("admin_users") + "?approved=1")

    @app.post("/admin/pending-patients/<int:pending_id>/accept")
    @admin_required
    def admin_accept_pending_patient(pending_id: int):
        with get_engine().connect() as con:
            pp = con.execute(
                text(
                    "SELECT id, user_id, last_name, first_name, date_of_birth, profession, phone, other_phone_1, other_phone_2, "
                    "address, insurance, matricule, fiche_1, fiche_2, fiche_3, fiche_4, fiche_5, fiche_6, fiche_7, fiche_8, fiche_9, fiche_10 "
                    "FROM pending_patients WHERE id = :id AND status = 'pending'"
                ),
                {"id": pending_id},
            ).mappings().first()
        if not pp:
            return redirect(url_for("admin_pending_patients"))
        pp = dict(pp)
        patient_cols = ["last_name", "first_name", "date_of_birth", "profession", "phone", "other_phone_1", "other_phone_2",
                       "address", "insurance", "matricule", "fiche_1", "fiche_2", "fiche_3", "fiche_4", "fiche_5",
                       "fiche_6", "fiche_7", "fiche_8", "fiche_9", "fiche_10"]
        row_clean = {k: pp[k] for k in patient_cols}
        with get_engine().begin() as con:
            r = con.execute(get_metadata().tables["patients"].insert().returning(get_metadata().tables["patients"].c.id), row_clean)
            new_patient_id = r.scalar_one()
            con.execute(
                text(
                    "INSERT INTO consultations (patient_id, consultation_date, consultation_detail, montant_acte, montant_recu) "
                    "SELECT :new_pid, consultation_date, consultation_detail, montant_acte, montant_recu FROM pending_consultations WHERE pending_patient_id = :pid"
                ),
                {"new_pid": new_patient_id, "pid": pending_id},
            )
            con.execute(
                text("UPDATE pending_patients SET status = 'accepted', reviewed_at = :now, reviewed_by = :by WHERE id = :id"),
                {"id": pending_id, "now": datetime.now(timezone.utc), "by": session.get("user_id")},
            )
        _log_action(
            session.get("user_id"),
            session.get("username") or "?",
            "pending_patient_accepted",
            "patient",
            new_patient_id,
            f"Patient en transit accepté: {pp['last_name']} {pp['first_name']} → patient #{new_patient_id}",
        )
        return redirect(url_for("admin_pending_patients") + "?pending_accepted=1")

    @app.post("/admin/pending-patients/<int:pending_id>/reject")
    @admin_required
    def admin_reject_pending_patient(pending_id: int):
        with get_engine().connect() as con:
            pp = con.execute(
                text("SELECT id, last_name, first_name, user_id FROM pending_patients WHERE id = :id AND status = 'pending'"),
                {"id": pending_id},
            ).mappings().first()
        if not pp:
            return redirect(url_for("admin_data"))
        with get_engine().begin() as con:
            con.execute(
                text("UPDATE pending_patients SET status = 'rejected', reviewed_at = :now, reviewed_by = :by WHERE id = :id"),
                {"id": pending_id, "now": datetime.now(timezone.utc), "by": session.get("user_id")},
            )
        _log_action(
            session.get("user_id"),
            session.get("username") or "?",
            "pending_patient_rejected",
            "pending_patient",
            pending_id,
            f"Patient en transit refusé: {pp['last_name']} {pp['first_name']}",
        )
        return redirect(url_for("admin_pending_patients") + "?pending_rejected=1")

    @app.post("/admin/modification-requests/<int:req_id>/accept")
    @admin_required
    def admin_accept_modification(req_id: int):
        with get_engine().connect() as con:
            row = con.execute(
                text(
                    "SELECT mr.id, mr.request_type, mr.record_id, mr.user_id, mr.proposed_data, u.username AS requester_username "
                    "FROM modification_requests mr JOIN users u ON u.id = mr.user_id WHERE mr.id = :id AND mr.status = 'pending'"
                ),
                {"id": req_id},
            ).mappings().first()
        if not row:
            return redirect(url_for("admin_modifications"))
        data = json.loads(row["proposed_data"])
        requester = row.get("requester_username") or "?"
        with get_engine().begin() as con:
            if row["request_type"] == "new_patient":
                pt = get_metadata().tables["patients"]
                patient_columns = {c.name for c in pt.c if c.name not in ("id", "created_at")}
                row_clean = {k: v for k, v in data.items() if k in patient_columns}
                r = con.execute(pt.insert().returning(pt.c.id), row_clean)
                new_patient_id = r.scalar_one()
                con.execute(
                    text(
                        "UPDATE modification_requests SET status = 'accepted', reviewed_at = :now, reviewed_by = :by WHERE id = :id"
                    ),
                    {"id": req_id, "now": datetime.now(timezone.utc), "by": session.get("user_id")},
                )
                fields_str = ", ".join(PATIENT_FIELD_LABELS.get(k, k) for k in sorted(row_clean.keys()) if k in PATIENT_FIELD_LABELS)
                _log_action(
                    session.get("user_id"),
                    session.get("username") or "?",
                    "new_patient_accepted",
                    "patient",
                    new_patient_id,
                    f"Demandé par {requester}: {data.get('last_name')} {data.get('first_name')}. Champs: {fields_str}"[:500],
                )
                return redirect(url_for("admin") + "?modification_accepted=1")
            if row["request_type"] == "patient":
                con.execute(
                    text(
                        "UPDATE patients SET last_name=:ln, first_name=:fn, date_of_birth=:dob, profession=:prof, "
                        "phone=:ph, other_phone_1=:o1, other_phone_2=:o2, address=:addr, insurance=:ins, matricule=:mat, "
                        "fiche_1=:f1, fiche_2=:f2, fiche_3=:f3, fiche_4=:f4, fiche_5=:f5, fiche_6=:f6, fiche_7=:f7, "
                        "fiche_8=:f8, fiche_9=:f9, fiche_10=:f10 "
                        "WHERE id=:id"
                    ),
                    {
                        "id": row["record_id"], "ln": data.get("last_name"), "fn": data.get("first_name"),
                        "dob": data.get("date_of_birth"), "prof": data.get("profession"), "ph": data.get("phone"),
                        "o1": data.get("other_phone_1"), "o2": data.get("other_phone_2"), "addr": data.get("address"),
                        "ins": data.get("insurance"), "mat": data.get("matricule"),
                        "f1": data.get("fiche_1"), "f2": data.get("fiche_2"), "f3": data.get("fiche_3"),
                        "f4": data.get("fiche_4"), "f5": data.get("fiche_5"), "f6": data.get("fiche_6"),
                        "f7": data.get("fiche_7"), "f8": data.get("fiche_8"), "f9": data.get("fiche_9"),
                        "f10": data.get("fiche_10"),
                    },
                )
                fields_str = ", ".join(PATIENT_FIELD_LABELS.get(k, k) for k in sorted(data.keys()) if k in PATIENT_FIELD_LABELS)
                _log_action(
                    session.get("user_id"),
                    session.get("username") or "?",
                    "modification_accepted",
                    "patient",
                    row["record_id"],
                    f"Modification patient (demandée par {requester}). Champs modifiés: {fields_str}"[:500],
                )
            else:
                con.execute(
                    text(
                        "UPDATE consultations SET consultation_date=:d, consultation_detail=:det, montant_acte=:ma, montant_recu=:mr WHERE id=:id"
                    ),
                    {
                        "id": row["record_id"],
                        "d": data.get("consultation_date"),
                        "det": data.get("consultation_detail"),
                        "ma": data.get("montant_acte"),
                        "mr": data.get("montant_recu"),
                    },
                )
                fields_str = ", ".join(CONSULTATION_FIELD_LABELS.get(k, k) for k in sorted(data.keys()) if k in CONSULTATION_FIELD_LABELS)
                _log_action(
                    session.get("user_id"),
                    session.get("username") or "?",
                    "modification_accepted",
                    "consultation",
                    row["record_id"],
                    f"Modification consultation (demandée par {requester}). Champs modifiés: {fields_str}"[:500],
                )
            con.execute(
                text(
                    "UPDATE modification_requests SET status = 'accepted', reviewed_at = :now, reviewed_by = :by WHERE id = :id"
                ),
                {"id": req_id, "now": datetime.now(timezone.utc), "by": session.get("user_id")},
            )
        return redirect(url_for("admin") + "?modification_accepted=1")

    @app.post("/admin/modification-requests/<int:req_id>/reject")
    @admin_required
    def admin_reject_modification(req_id: int):
        with get_engine().connect() as con:
            row = con.execute(
                text(
                    "SELECT mr.request_type, mr.proposed_data, u.username FROM modification_requests mr JOIN users u ON u.id = mr.user_id WHERE mr.id = :id"
                ),
                {"id": req_id},
            ).mappings().first()
        with get_engine().begin() as con:
            con.execute(
                text(
                    "UPDATE modification_requests SET status = 'rejected', reviewed_at = :now, reviewed_by = :by WHERE id = :id"
                ),
                {"id": req_id, "now": datetime.now(timezone.utc), "by": session.get("user_id")},
            )
        if row:
            try:
                d = json.loads(row["proposed_data"] or "{}")
                summary = f"{d.get('last_name', '')} {d.get('first_name', '')}".strip() or row.get("username", "?")
                req_type = row.get("request_type")
                if req_type == "new_patient":
                    fields_str = ", ".join(PATIENT_FIELD_LABELS.get(k, k) for k in sorted(d.keys()) if k in PATIENT_FIELD_LABELS)
                elif req_type == "patient":
                    fields_str = ", ".join(PATIENT_FIELD_LABELS.get(k, k) for k in sorted(d.keys()) if k in PATIENT_FIELD_LABELS)
                elif req_type == "consultation":
                    fields_str = ", ".join(CONSULTATION_FIELD_LABELS.get(k, k) for k in sorted(d.keys()) if k in CONSULTATION_FIELD_LABELS)
                else:
                    fields_str = ""
                if fields_str:
                    summary = f"{summary}. Champs concernés: {fields_str}"
            except Exception:
                summary = row.get("username", "?")
            action_name = "new_patient_rejected" if row.get("request_type") == "new_patient" else "modification_rejected"
            _log_action(
                session.get("user_id"),
                session.get("username") or "?",
                action_name,
                "modification_request",
                req_id,
                f"Demandé par {row.get('username', '?')}: {summary}"[:500],
            )
        return redirect(url_for("admin") + "?modification_rejected=1")

    @app.post("/admin/users/<int:user_id>/delete")
    @admin_required
    def admin_delete_user(user_id: int):
        if user_id == session.get("user_id"):
            return redirect(url_for("admin_users") + "?error=impossible-supprimer-votre-compte")
        with get_engine().connect() as con:
            row = con.execute(text("SELECT is_admin FROM users WHERE id = :id"), {"id": user_id}).mappings().first()
            if not row:
                return redirect(url_for("admin_users"))
            if row["is_admin"]:
                count = con.execute(text("SELECT COUNT(*) FROM users WHERE is_admin = :v"), {"v": True}).scalar_one()
                if count <= 1:
                    return redirect(url_for("admin") + "?error=impossible-supprimer-dernier-admin")
        with get_engine().begin() as con:
            con.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        return redirect(url_for("admin_users"))

    @app.post("/reset-demo-data")
    @admin_required
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
        # Render, Railway, etc. exigent souvent SSL pour PostgreSQL
        if "postgresql" in url and "sslmode" not in url:
            if "render.com" in url or "railway.app" in url or "supabase" in url or "neon.tech" in url:
                sep = "&" if "?" in url else "?"
                url = url + sep + "sslmode=require"
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

    appointments = Table(
        "appointments",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "patient_id",
            Integer,
            ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("appointment_date", String(50), nullable=False),  # YYYY-MM-DD
        Column("appointment_time", String(10)),  # HH:MM optionnel
        Column("notes", String(500)),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    users = Table(
        "users",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("username", String(80), unique=True, nullable=False),
        Column("password_hash", String(255), nullable=False),
        Column("is_admin", Boolean, nullable=False),
        Column("is_approved", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    pending_confirmations = Table(
        "pending_confirmations",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("email", String(255), nullable=False),
        Column("username", String(80), nullable=False),
        Column("password_hash", String(255), nullable=False),
        Column("token", String(64), unique=True, nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    modification_requests = Table(
        "modification_requests",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("request_type", String(20), nullable=False),  # 'patient' | 'consultation'
        Column("record_id", Integer, nullable=False),
        Column("user_id", Integer, nullable=False),
        Column("proposed_data", String(5000), nullable=False),  # JSON
        Column("status", String(20), nullable=False),  # pending | accepted | rejected
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column("reviewed_at", DateTime(timezone=True), nullable=True),
        Column("reviewed_by", Integer, nullable=True),
    )

    action_log = Table(
        "action_log",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, nullable=True),
        Column("username", String(80), nullable=False),
        Column("action", String(80), nullable=False),
        Column("entity_type", String(30), nullable=True),
        Column("entity_id", Integer, nullable=True),
        Column("details", String(500), nullable=True),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    # Patients en attente (table de transition) : l'utilisateur peut les modifier et ajouter des consultations
    # jusqu'à ce que l'admin accepte ou refuse
    pending_patients = Table(
        "pending_patients",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, nullable=False),
        Column("status", String(20), nullable=False),  # pending | accepted | rejected
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column("reviewed_at", DateTime(timezone=True), nullable=True),
        Column("reviewed_by", Integer, nullable=True),
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
    )
    pending_consultations = Table(
        "pending_consultations",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "pending_patient_id",
            Integer,
            ForeignKey("pending_patients.id", ondelete="CASCADE"),
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
    Index("idx_consultations_patient_date", consultations.c.patient_id, consultations.c.consultation_date)
    Index("idx_appointments_date", appointments.c.appointment_date)
    Index("idx_action_log_created_at", action_log.c.created_at)
    Index("idx_pending_patients_user_status", pending_patients.c.user_id, pending_patients.c.status)

    return md


# Libellés français des champs (pour l'historique des modifications)
PATIENT_FIELD_LABELS = {
    "last_name": "Nom",
    "first_name": "Prénom",
    "date_of_birth": "Date de naissance",
    "profession": "Profession",
    "phone": "Téléphone",
    "other_phone_1": "Autre téléphone 1",
    "other_phone_2": "Autre téléphone 2",
    "address": "Adresse",
    "insurance": "Assurance",
    "matricule": "Matricule",
    **{f"fiche_{i}": f"Fiche #{i}" for i in range(1, 11)},
}
CONSULTATION_FIELD_LABELS = {
    "consultation_date": "Date consultation",
    "consultation_detail": "Détail consultation",
    "montant_acte": "Montant acte",
    "montant_recu": "Montant reçu",
}


def _normalize_val(v: Any) -> str | None:
    """Pour comparaison: chaîne vide ou None → None."""
    if v is None:
        return None
    s = (v if isinstance(v, str) else str(v)).strip()
    return s if s else None


def _changed_field_labels(
    old_data: dict[str, Any],
    new_data: dict[str, Any],
    field_labels: dict[str, str],
) -> list[str]:
    """Retourne la liste des libellés français des champs dont la valeur a changé."""
    changed = []
    for key, label in field_labels.items():
        if key not in new_data:
            continue
        old_v = _normalize_val(old_data.get(key))
        new_v = _normalize_val(new_data.get(key))
        if old_v != new_v:
            changed.append(label)
    return changed


def _log_action(
    user_id: int | None,
    username: str,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: str | None = None,
) -> None:
    """Enregistre une action dans l'historique (sauf recherche)."""
    with get_engine().begin() as con:
        con.execute(
            text(
                "INSERT INTO action_log (user_id, username, action, entity_type, entity_id, details) "
                "VALUES (:uid, :uname, :act, :etype, :eid, :details)"
            ),
            {
                "uid": user_id,
                "uname": username or "?",
                "act": action,
                "etype": entity_type,
                "eid": entity_id,
                "details": details[:500] if details else None,
            },
        )


def init_db(force_reset: bool = False) -> None:
    engine = get_engine()
    md = get_metadata()
    if force_reset:
        md.drop_all(engine, checkfirst=True)
    md.create_all(engine, checkfirst=True)

    # Migration: ajouter is_approved si la table users existe déjà sans cette colonne
    with engine.connect() as con:
        try:
            con.execute(text("SELECT is_approved FROM users LIMIT 1"))
        except Exception:
            con.rollback()
            try:
                con.execute(text("ALTER TABLE users ADD COLUMN is_approved BOOLEAN"))
                con.commit()
            except Exception:
                con.rollback()
            with engine.begin() as c2:
                c2.execute(text("UPDATE users SET is_approved = :v WHERE is_approved IS NULL"), {"v": True})

    # Migration: supprimer la colonne identifiant_final des bases existantes
    db_url = get_database_url()
    with engine.connect() as con:
        try:
            if "postgresql" in db_url:
                con.execute(text("ALTER TABLE patients DROP COLUMN IF EXISTS identifiant_final"))
                con.commit()
            else:
                # SQLite 3.35+ supporte DROP COLUMN
                info = con.execute(text("PRAGMA table_info(patients)")).fetchall()
                cols = [row[1] for row in info]
                if "identifiant_final" in cols:
                    con.execute(text("ALTER TABLE patients DROP COLUMN identifiant_final"))
                    con.commit()
        except Exception:
            con.rollback()

    # Migration: supprimer identifiant_1 et identifiant_2 des bases existantes
    with engine.connect() as con:
        try:
            if "postgresql" in db_url:
                con.execute(text("ALTER TABLE patients DROP COLUMN IF EXISTS identifiant_1"))
                con.execute(text("ALTER TABLE patients DROP COLUMN IF EXISTS identifiant_2"))
                con.commit()
            else:
                for col in ("identifiant_1", "identifiant_2"):
                    info = con.execute(text("PRAGMA table_info(patients)")).fetchall()
                    cols = [row[1] for row in info]
                    if col in cols:
                        con.execute(text(f"ALTER TABLE patients DROP COLUMN {col}"))
                        con.commit()
        except Exception:
            con.rollback()

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
