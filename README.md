# Site web + base de données — Démo (SQLite / PostgreSQL)

Ce projet est un exemple simple d’un site web relié à une base de données, où tu peux:

- **Faire une recherche patients** (nom/prénom/téléphone/matricule/identifiant) et voir les résultats
- **Exécuter une requête SQL en lecture seule** (SELECT / WITH ... SELECT) et voir les résultats

## Prérequis

- Python 3.10+ (idéalement 3.11+)

## Installation (Windows / PowerShell)

Dans un terminal:

```powershell
cd "C:\Users\Admin\Downloads\site_db_flask"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Puis ouvre `http://127.0.0.1:5000`.

## Base de données

- **Par défaut (dev)**: SQLite dans `data/app.db`
- **Production**: PostgreSQL — AWS RDS ou services plus simples (Supabase, Neon, Railway, Render ; voir ci-dessous)
- Tables: `patients`, `consultations`

### Choix de base (via `DATABASE_URL`)

Le projet lit la variable d’environnement `DATABASE_URL`.

- Si `DATABASE_URL` est **absente**, il utilise SQLite local.
- Si `DATABASE_URL` est **présente**, il se connecte à la base indiquée (ex: PostgreSQL sur AWS).

Exemples:

```powershell
# SQLite (par défaut, rien à faire)

# PostgreSQL (AWS RDS) — exemple
$env:DATABASE_URL = "postgresql+psycopg://db_user:db_password@db-hostname:5432/db_name"
python app.py
```

> Important: évite de mettre le mot de passe “en dur” dans le code. Utilise un secret/variable d’environnement sur AWS.

**Confirmation par email** : pour les demandes d'accès, l'app envoie un email de confirmation (lien valable 24 h). Voir la section [Configurer l'envoi d'emails](#configurer-lenvoi-demails) ci-dessous. Sans config (ex. en local), la page affiche le lien à cliquer en mode dev.

### Configurer l'envoi d'emails

Pour que les demandes d'accès déclenchent un vrai email de confirmation, définis ces **variables d'environnement** :

| Variable        | Description                          | Exemple                    |
|----------------|--------------------------------------|----------------------------|
| `MAIL_SERVER`  | Serveur SMTP                         | `smtp.gmail.com`           |
| `MAIL_PORT`    | Port (souvent 587 avec TLS)          | `587`                      |
| `MAIL_USE_TLS` | Activer TLS                          | `true`                     |
| `MAIL_USERNAME`| Identifiant SMTP                     | ton adresse ou login       |
| `MAIL_PASSWORD`| Mot de passe SMTP                    | mot de passe ou "App Password" |
| `MAIL_FROM`    | Adresse expéditrice affichée         | `noreply@mondomaine.com`   |

**Que utiliser comme serveur mail ?**

- **Gmail** (simple pour tester)  
  - `MAIL_SERVER=smtp.gmail.com`, `MAIL_PORT=587`, `MAIL_USE_TLS=true`  
  - Utilise un **mot de passe d'application** (pas ton mot de passe Gmail) : [Compte Google](https://myaccount.google.com/) → Sécurité → Validation en 2 étapes activée → Mots de passe des applications → Générer.  
  - `MAIL_USERNAME` = ton adresse Gmail, `MAIL_PASSWORD` = le mot de passe d'application.

- **SendGrid** (gratuit jusqu'à ~100 emails/jour)  
  - Créer un compte sur [sendgrid.com](https://sendgrid.com), puis créer une **clé API** (API Keys).  
  - `MAIL_SERVER=smtp.sendgrid.net`, `MAIL_PORT=587`, `MAIL_USE_TLS=true`  
  - `MAIL_USERNAME=apikey`, `MAIL_PASSWORD=ta_cle_api_sendgrid`, `MAIL_FROM=adresse_verifiee@mondomaine.com`.

- **Brevo (ex Sendinblue)** (gratuit jusqu'à 300 emails/jour)  
  - Compte sur [brevo.com](https://www.brevo.com), puis SMTP & API → Paramètres SMTP.  
  - `MAIL_SERVER=smtp-relay.brevo.com`, `MAIL_PORT=587`, `MAIL_USE_TLS=true`  
  - `MAIL_USERNAME` = ton email de connexion Brevo, `MAIL_PASSWORD` = clé SMTP (générée dans le compte).

- **Mailjet, Amazon SES, OVH…**  
  - Même principe : récupère serveur SMTP, port, identifiant et mot de passe dans la doc du fournisseur, puis remplis les variables ci-dessus.

**Exemple (PowerShell, Gmail)** :

```powershell
$env:MAIL_SERVER = "smtp.gmail.com"
$env:MAIL_PORT = "587"
$env:MAIL_USE_TLS = "true"
$env:MAIL_USERNAME = "ton.email@gmail.com"
$env:MAIL_PASSWORD = "ton_mot_de_passe_application"
$env:MAIL_FROM = "ton.email@gmail.com"
python app.py
```

En production (Render, Railway, etc.), ajoute ces variables dans les **Environment** / **Variables** du projet ; ne mets jamais le mot de passe dans le code. Un fichier **`.env.example`** à la racine du projet liste toutes les variables possibles (copier et renommer en `.env` puis remplir, ou s'en inspirer pour définir les variables à la main).

### Numériser des fiches vers Google Drive

Pour pouvoir importer des fiches (PDF, images) directement vers ton Drive depuis les formulaires patient :

1. Créer un projet sur [Google Cloud Console](https://console.cloud.google.com/), activer l’API Google Drive
2. Créer un **compte de service** (IAM & Admin → Comptes de service), télécharger le fichier JSON
3. Créer un dossier dans ton Drive, le partager avec l’email du compte de service (ex. `xxx@xxx.iam.gserviceaccount.com`) avec le rôle **Éditeur**
4. Récupérer l’ID du dossier depuis l’URL : `https://drive.google.com/drive/folders/FOLDER_ID`
5. Variables d’environnement :
   - `GOOGLE_DRIVE_CREDENTIALS_JSON` : chemin vers le fichier JSON
   - `GOOGLE_DRIVE_FOLDER_ID` : ID du dossier partagé

Sans cette config, le bouton « Importer » affiche une erreur, mais tu peux toujours coller une URL manuellement.

### Alternatives plus simples qu'AWS (BD hébergée en quelques clics)

Si tu veux **héberger ta base PostgreSQL** sans gérer EC2, VPC ou groupes de sécurité, ces services donnent une **URL de connexion** (`DATABASE_URL`) en quelques minutes :

| Service | Ce que c'est | Pourquoi c'est simple |
|--------|----------------|------------------------|
| **[Supabase](https://supabase.com)** | PostgreSQL managé + tableau de bord | Gratuit (quota généreux), inscription → projet → on te donne l'URL de connexion. Tu la mets dans `DATABASE_URL` et c'est tout. |
| **[Neon](https://neon.tech)** | PostgreSQL serverless | Gratuit, inscription → créer une base → copier l'URL. Idéal pour dev / petit projet. |
| **[Railway](https://railway.app)** | App + PostgreSQL sur une même plateforme | Tu déploies ton site ET tu ajoutes une base en un clic ; `DATABASE_URL` est créée automatiquement. |
| **[Render](https://render.com)** | Hébergement web + PostgreSQL gratuit | Créer une "PostgreSQL" → on te donne l'URL. Tu peux aussi héberger ton app Flask sur Render. |

### Déploiement avec Supabase

Tu veux utiliser **Supabase** : base PostgreSQL hébergée, accessible depuis partout avec une seule URL. L’app convertit automatiquement l’URL Supabase (`postgresql://`) en format psycopg.

**1. Créer un projet Supabase**

- Va sur [supabase.com](https://supabase.com) → **Start your project** (ou connexion si tu as déjà un compte).
- **New project** : choisis un nom, un mot de passe pour l’utilisateur `postgres` (note-le bien), une région. Valide.

**2. Récupérer l’URL de connexion**

- Dans le projet : menu **Settings** (icône engrenage) → **Database**.
- Descends jusqu’à **Connection string**.
- Onglet **URI** : tu vois une URL du type  
  `postgresql://postgres.[ref]:[TON_MOT_DE_PASSE]@aws-0-[region].pooler.supabase.com:6543/postgres`  
  ou (mode direct)  
  `postgresql://postgres:[TON_MOT_DE_PASSE]@db.[ref].supabase.co:5432/postgres`.
- Clique sur **Copy** pour copier l’URL. Tu peux garder `postgresql://` : l’app la convertit en `postgresql+psycopg://` toute seule.

**3. Lancer le site sur ton PC**

Dans le dossier du projet (PowerShell) :

```powershell
cd "C:\Users\Admin\Downloads\site_db_flask"
$env:DATABASE_URL = "postgresql://postgres.[ref]:TON_MOT_DE_PASSE@aws-0-xxx.pooler.supabase.com:6543/postgres"
.\.venv\Scripts\python.exe app.py
```

(Colle ta vraie URL à la place de l’exemple. Si tu as un `.venv`, utilise `.\.venv\Scripts\python.exe` ; sinon `python`.)

Ouvre `http://127.0.0.1:5000`. Au premier lancement, l’app crée les tables `patients` et `consultations` dans Supabase.

**4. Importer ton Excel dans Supabase**

Avec la **même** `DATABASE_URL` (même session PowerShell ou redéfinis-la) :

```powershell
$env:DATABASE_URL = "postgresql://postgres.[ref]:TON_MOT_DE_PASSE@..."
.\.venv\Scripts\python.exe -m scripts.import_excel "C:\chemin\vers\ton_fichier.xlsx"
```

Les lignes partent dans la base Supabase. Recharge le site pour voir les patients.

**5. (Optionnel) Héberger le site ailleurs**

Tu peux faire tourner le site sur un autre hébergeur (Render, Railway, etc.) : définis simplement la variable d’environnement **`DATABASE_URL`** avec la même URL Supabase (celle de l’étape 2). La base reste sur Supabase ; le site se connecte à distance.

**Résumé Supabase** : **Nouveau projet** → **Settings** → **Database** → **Connection string (URI)** → copier l’URL → `DATABASE_URL` sur ton PC (ou sur l’hébergeur) → lancer l’app et/ou le script d’import Excel.

### Héberger le site en ligne rapidement (Render + Supabase)

Tu as déjà la base sur **Supabase** et tu veux mettre le **site en ligne** en quelques minutes : utilise **Render** (gratuit, sans carte bancaire pour le tier gratuit).

**1. Mettre le projet sur GitHub**

- Crée un dépôt sur [github.com](https://github.com) (ex. `site_db_flask`).
- Depuis ton PC, dans le dossier du projet :
  ```powershell
  cd "C:\Users\Admin\Downloads\site_db_flask"
  git init
  git add .
  git commit -m "Initial"
  git remote add origin https://github.com/TON_COMPTE/site_db_flask.git
  git push -u origin main
  ```
  (Remplace `TON_COMPTE` et le nom du repo si besoin. Si le dépôt existe déjà, `git remote add` peut indiquer que l’origin existe déjà.)

**2. Créer un Web Service sur Render**

- Va sur [render.com](https://render.com) → **Sign up** (ou connexion) → **Dashboard**.
- **New +** → **Web Service**.
- **Connect a repository** : connecte ton compte GitHub, choisis le dépôt du projet (ex. `site_db_flask`).
- **Name** : par ex. `site-patients`.
- **Region** : choisis la plus proche (ex. Frankfurt).
- **Branch** : `main`.
- **Runtime** : **Python 3**.
- **Build Command** : `pip install -r requirements.txt`
- **Start Command** : `gunicorn -w 2 -b 0.0.0.0:$PORT "app:app"`

**3. Ajouter la variable d’environnement (Supabase)**

- Dans la même page, section **Environment** → **Add Environment Variable**.
- **Key** : `DATABASE_URL`
- **Value** : ton URL Supabase (Session Pooler), ex.  
  `postgresql://postgres.izbfkcdgieyhtppflezr:TON_MOT_DE_PASSE@aws-1-ca-central-1.pooler.supabase.com:5432/postgres`  
  (colle l’URL exacte de Supabase, avec le bon mot de passe.)

**4. Déployer**

- Clique sur **Create Web Service**. Render va builder puis lancer l’app (1 à 3 min).
- Une fois le déploiement terminé, tu obtiens une URL du type **https://site-patients.onrender.com**. Ouvre-la : le site est en ligne et connecté à Supabase.

**Note** : sur le plan gratuit, le service “s’endort” après une période d’inactivité ; la première visite après ça peut prendre 30–60 s pour redémarrer.

En résumé : **GitHub** (push du code) → **Render** (New Web Service, repo, Build/Start, `DATABASE_URL` = URL Supabase) → site en ligne.

### Déploiement sur Railway (compte créé)

Tu as un compte **Railway** : tu peux héberger la base PostgreSQL **et** le site Flask sur la même plateforme. L’app accepte automatiquement l’URL `postgresql://` fournie par Railway (conversion en `postgresql+psycopg://`).

**1. Créer un projet et une base PostgreSQL**

- [railway.app](https://railway.app) → **New Project**.
- **Add service** → **Database** → **PostgreSQL**. Railway crée la base et expose une variable (ex. `DATABASE_URL` ou `POSTGRES_URL`).

**2. Déployer le site Flask**

- **Add service** → **GitHub Repo** (si le projet est sur GitHub) : choisis le dépôt `site_db_flask` (ou le nom du repo).
- Ou **Empty Service** puis déploiement avec le [Railway CLI](https://docs.railway.app/develop/cli) : `railway up` depuis le dossier du projet.

**3. Brancher la base à l’app**

- Clique sur le **service Flask** (ton app).
- **Variables** → **Add variable** ou **Reference** :
  - Si Railway te propose de **référencer** la variable du service PostgreSQL (ex. `DATABASE_URL`), utilise cette référence. L’app convertit tout seul `postgresql://` en `postgresql+psycopg://`.
  - Sinon, dans le service PostgreSQL, onglet **Variables** ou **Connect**, copie l’**URL de connexion**. Dans le service Flask, crée une variable `DATABASE_URL` avec cette valeur (tu peux laisser `postgresql://`, l’app l’adapte).

**4. Démarrer l’app**

- Le projet contient un **Procfile** : `web: gunicorn -w 2 -b 0.0.0.0:$PORT "app:app"`. Railway utilise `PORT` automatiquement ; inutile de le définir.
- Si aucun Procfile n’est détecté : dans le service Flask, **Settings** → **Deploy** → **Start Command** :  
  `gunicorn -w 2 -b 0.0.0.0:$PORT "app:app"`

**5. Domaine public**

- Dans le service Flask : **Settings** → **Networking** → **Generate domain**. Tu obtiens une URL du type `https://ton-app.up.railway.app`.

**6. Importer ton Excel**

- Option A : depuis ton PC avec l’URL Railway de la base (si tu l’as copiée) :  
  `$env:DATABASE_URL = "postgresql+psycopg://..."` puis `python -m scripts.import_excel ton_fichier.xlsx`.
- Option B : si la base n’est accessible que depuis Railway, importer d’abord en local (SQLite), puis exporter les données et les importer via un script ou la console SQL du site une fois en ligne.

En résumé : **New Project** → **PostgreSQL** + **service GitHub (ou Empty)** → lier `DATABASE_URL` au Postgres → générer le domaine. Le site et la base tournent sur Railway.

## Endpoints utiles

- Page recherche: `/`
- Détail patient: `/patients/<id>`
- Console SQL (lecture seule): `/sql`
- API JSON: `/api/patients?last_name=dupont&limit=50`

## Objectif AWS (recommandé): RDS PostgreSQL

### 1) Créer la base sur AWS

- **Service**: Amazon RDS
- **Moteur**: PostgreSQL
- **Réseau**: idéalement l’appli et la base dans le **même VPC**
- **Sécurité**:
  - Ne pas rendre la base “publicly accessible” en production
  - Autoriser le port **5432** uniquement depuis le serveur/app (Security Group)

### 2) Récupérer l’endpoint RDS et définir `DATABASE_URL`

Dans ton environnement d’exécution (EC2 / Elastic Beanstalk / ECS / Lightsail / etc), définis:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://db_user:db_password@<endpoint-rds>:5432/db_name"
```

### 3) Initialiser le schéma

Au démarrage, l’appli crée automatiquement les tables si elles n’existent pas encore.

### 4) Héberger le site (au choix)

- **Simple**: EC2 + `gunicorn` derrière Nginx
- **Managed**: Elastic Beanstalk (très pratique pour débuter)
- **Container**: ECS/Fargate

Si tu me dis **comment tu veux héberger le site** (EC2 / Beanstalk / ECS / autre), je te prépare la config exacte (variables d’env, commande de démarrage, sécurité).

## Déploiement EC2 + RDS (tutoriel AWS : instance privée)

Tu as suivi le tutoriel AWS : **EC2** (`ec2-database-connect`) + **RDS PostgreSQL** (`database-test1`) en **sous-réseau privé**. Seule l’EC2 (dans le même VPC) peut joindre la base. Le site Flask doit donc **tourner sur l’EC2** et utiliser `DATABASE_URL` pour se connecter au RDS.

### 1) Récupérer l’endpoint RDS et le mot de passe

- Console AWS → **RDS** → **Bases de données** → clique sur `database-test1`.
- Note l’**endpoint** (ex: `database-test1.xxxx.eu-west-1.rds.amazonaws.com`).
- Note le **nom d’utilisateur** (souvent `postgres`) et le **mot de passe** que tu as choisi (ou généré). La base par défaut créée par Easy Create s’appelle en général **`postgres`**.

Construis l’URL (remplace par tes valeurs) :

```text
postgresql+psycopg://postgres:TON_MOT_DE_PASSE@endpoint-rds:5432/postgres
```

Exemple :

```text
postgresql+psycopg://postgres:MonMotDePasse123@database-test1.xxxx.eu-west-1.rds.amazonaws.com:5432/postgres
```

### 2) Se connecter en SSH à l’EC2

Depuis ton PC (PowerShell ou terminal) :

```bash
ssh -i "chemin/vers/ta-paire-de-cles.pem" ec2-user@<DNS-public-EC2>
```

Le **DNS public** de l’EC2 est dans la console EC2 → Instances → ton instance → “Nom DNS public (IPv4)”. Sous **Amazon Linux 2023**, l’utilisateur SSH est **`ec2-user`**.

### 3) Installer Python et les outils sur l’EC2

Sur l’EC2 (une fois connecté en SSH) :

```bash
sudo dnf install -y python3.11 python3.11-pip git
```

(Vérifie la version disponible : `dnf list python3*` ; sinon `python3` suffit.)

### 4) Copier le projet sur l’EC2

**Option A – Depuis ton PC (SCP)** : depuis le dossier où se trouve `site_db_flask` sur ton PC :

```powershell
scp -i "chemin/vers/ta-paire-de-cles.pem" -r site_db_flask ec2-user@<DNS-public-EC2>:~/
```

**Option B – Git** : si le projet est dans un dépôt, sur l’EC2 :

```bash
git clone <url-de-ton-repo> site_db_flask
cd site_db_flask
```

### 5) Environnement virtuel et dépendances sur l’EC2

Sur l’EC2 :

```bash
cd ~/site_db_flask
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(Si tu as seulement `python3`, remplace par `python3 -m venv .venv`.)

### 6) Définir DATABASE_URL et lancer le site

Sur l’EC2, avec la même URL que ci‑dessus :

```bash
export DATABASE_URL="postgresql+psycopg://postgres:TON_MOT_DE_PASSE@endpoint-rds:5432/postgres"
python app.py
```

Pour un test rapide : le site écoute sur le port 5000. Pour y accéder depuis ton navigateur, il faut **autoriser le port 5000** (ou 80) dans le **groupe de sécurité** de l’EC2 :

- Console AWS → **EC2** → **Groupes de sécurité** → groupe attaché à ton instance → **Règles de trafic entrant** → **Modifier** → **Ajouter une règle** : Type **Personnalisé TCP**, Port **5000**, Source **0.0.0.0/0** (ou ton IP pour plus de sécurité). Enregistrer.

Puis ouvre dans ton navigateur : `http://<DNS-public-EC2>:5000`.

**Pour la production** (optionnel) : lancer avec **gunicorn** et garder la variable d’environnement :

```bash
export DATABASE_URL="postgresql+psycopg://postgres:TON_MOT_DE_PASSE@endpoint-rds:5432/postgres"
gunicorn -w 2 -b 0.0.0.0:5000 "app:app"
```

(Pour mettre le mot de passe dans un fichier ou un secret, voir la section “Choix de base” plus haut.)

### 7) Importer ton Excel dans la base (RDS privée)

Comme le RDS n’est accessible **que depuis l’EC2**, il faut lancer l’import **sur l’EC2** :

1. **Copier le fichier Excel sur l’EC2** (depuis ton PC) :

   ```powershell
   scp -i "chemin/vers/ta-paire-de-cles.pem" "C:\chemin\vers\ton_fichier.xlsx" ec2-user@<DNS-public-EC2>:~/site_db_flask/
   ```

2. **Sur l’EC2**, avec le même `DATABASE_URL` que le site :

   ```bash
   cd ~/site_db_flask
   source .venv/bin/activate
   export DATABASE_URL="postgresql+psycopg://postgres:TON_MOT_DE_PASSE@endpoint-rds:5432/postgres"
   python -m scripts.import_excel ~/site_db_flask/ton_fichier.xlsx
   ```

Ensuite, recharge la page du site : les patients importés doivent apparaître dans la recherche.

### Résumé (EC2 + RDS privé)

| Étape | Où | Action |
|-------|-----|--------|
| 1 | Console RDS | Noter endpoint, user, mot de passe, base `postgres` |
| 2 | PC | SSH vers EC2 |
| 3 | EC2 | Installer Python 3, pip, (git) |
| 4 | PC ou EC2 | Copier/cloner le projet `site_db_flask` sur l’EC2 |
| 5 | EC2 | `pip install -r requirements.txt` dans un venv |
| 6 | EC2 | `export DATABASE_URL=...` puis `python app.py` ou `gunicorn ...` |
| 7 | Console EC2 | Ouvrir le port 5000 (ou 80) dans le groupe de sécurité |
| 8 | EC2 | Importer Excel : copier le fichier sur l’EC2, lancer `python -m scripts.import_excel ...` avec le même `DATABASE_URL` |

La base est **privée** : seul l’EC2 du même VPC peut s’y connecter ; ton PC ne peut pas atteindre le RDS directement, d’où l’import depuis l’EC2.

## Mettre ta base Excel sur AWS et la connecter au site

### En résumé

1. **Créer une base PostgreSQL sur AWS** (RDS), comme ci‑dessus.
2. **Connecter le site** à cette base avec `DATABASE_URL` (l’app crée les tables au démarrage).
3. **Importer ton fichier Excel** dans la base avec le script fourni, puis lancer le site.

### Étapes détaillées

#### 1) Créer la base sur AWS (RDS PostgreSQL)

- Dans la console AWS → **RDS** → **Créer une base de données**
- Moteur : **PostgreSQL**
- Nom d’utilisateur et mot de passe : à retenir pour `DATABASE_URL`
- Base créée : note l’**endpoint** (ex: `ma-base.xxxx.eu-west-1.rds.amazonaws.com`) et le **port** (5432)

#### 2) Connecter le site à la base AWS

Sur la machine où tu lances le site (PC pour test, ou serveur EC2/Beanstalk plus tard) :

```powershell
# Exemple (remplace par ton utilisateur, mot de passe, endpoint et nom de base)
$env:DATABASE_URL = "postgresql+psycopg://mon_user:MonMotDePasse@ma-base.xxxx.eu-west-1.rds.amazonaws.com:5432/ma_base"
python app.py
```

Au premier lancement, l’app crée les tables `patients` et `consultations` si elles n’existent pas.

#### 3) Importer ton Excel dans la base

Le script `scripts/import_excel.py` lit un fichier Excel et insère les lignes dans les tables `patients` (et éventuellement `consultations` si les colonnes sont présentes).

**Depuis la racine du projet** (avec la même `DATABASE_URL` que le site) :

```powershell
# Toujours avec la même DATABASE_URL si tu vises AWS
$env:DATABASE_URL = "postgresql+psycopg://..."
python -m scripts.import_excel "C:\chemin\vers\ton_fichier.xlsx"
```

- **Première feuille** du classeur utilisée par défaut.
- **Correspondance des colonnes** : le script reconnaît des en-têtes en français (Nom du patient, Prénom du patient, Date de naissance, Téléphone, Matricule, Adresse, Assurance, Fiche #1…#10, Identifiant 1/2/final, Date de consultation 1, Détail de la consultation, Montant acte, Montant reçu, etc.). Tu peux modifier le mapping dans `scripts/import_excel.py` (dictionnaires `COLUMN_MAPPING_PATIENTS` et `COLUMN_MAPPING_CONSULTATION`).
- Si une ligne contient aussi des colonnes de consultation (date, détail, montants), une entrée dans `consultations` est créée pour ce patient.

**Sans `DATABASE_URL`** (SQLite local) :

```powershell
python -m scripts.import_excel "C:\chemin\vers\ton_fichier.xlsx"
```

Les données partent dans `data/app.db`. Tu peux ensuite soit continuer en local, soit exporter/importer cette base vers PostgreSQL (AWS) si besoin.

#### 4) Vérifier

- Relance le site (`python app.py`) et ouvre la page de recherche.
- Renseigne un filtre (nom, prénom, etc.) : les patients importés doivent apparaître.

En résumé : **Excel → script d’import → base (SQLite ou PostgreSQL sur AWS) ; le site se connecte à cette base avec `DATABASE_URL`.**

## Adapter à ta propre base

Dis-moi:

- ton SGBD (**SQLite / MySQL / PostgreSQL**)
- ton schéma (tables/colonnes)
- les écrans voulus (filtres, tri, pagination, export CSV, authentification)

…et je te l’adapte proprement (connexion, requêtes, UI).
