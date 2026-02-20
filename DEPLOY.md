# Déploiement en production

## Prérequis
- Code à jour (modifs commitées)
- Compte Render / Railway / hébergeur
- `DATABASE_URL` (PostgreSQL, ex. Supabase)

## Option 1 : Render (recommandé, gratuit)

1. **Pousser sur GitHub**
   ```powershell
   cd "c:\Users\Admin\Downloads\site_db_flask"
   git status
   git add .
   git commit -m "Deploy: fiches Drive, imports, nav design"
   git push origin main
   ```

2. **Render** → [render.com](https://render.com) → New Web Service
   - Connecte le dépôt GitHub
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn -w 2 -b 0.0.0.0:$PORT "app:app"`

3. **Variables d'environnement** (Environment)
   | Variable | Valeur |
   |----------|--------|
   | `DATABASE_URL` | URL PostgreSQL (Supabase, etc.) |
   | `SECRET_KEY` | Chaîne secrète longue (ex. générée) |
   | `GOOGLE_DRIVE_CREDENTIALS_JSON` | (optionnel) Chemin ou contenu JSON |
   | `GOOGLE_DRIVE_FOLDER_ID` | (optionnel) ID dossier Drive |

4. **Deploy** → le site sera en ligne après le build.

## Option 2 : Railway

1. Push sur GitHub (comme ci-dessus)
2. [railway.app](https://railway.app) → New Project → Add GitHub Repo
3. Variables: `DATABASE_URL`, `SECRET_KEY`
4. Procfile utilisé automatiquement

## Option 3 : Commande manuelle (git push)

```powershell
cd "c:\Users\Admin\Downloads\site_db_flask"
git add .
git status
git commit -m "Deploy production"
git push origin main
```

Si ton hébergeur est déjà branché sur le dépôt, le push déclenche le déploiement.
