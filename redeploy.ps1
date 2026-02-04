# Redéploiement : commit + push vers GitHub (Render redéploie automatiquement)
# Exécuter depuis le dossier du projet : .\redeploy.ps1

$msg = if ($args[0]) { $args[0] } else { "Redéploiement: corrections deploy Render" }

git status
git add -A
git status
git commit -m $msg
git push origin main

Write-Host "`nPush terminé. Si Render est connecté au repo, le déploiement se lance automatiquement."
Write-Host "Sinon : Render Dashboard -> ton service -> Manual Deploy -> Deploy latest commit."
