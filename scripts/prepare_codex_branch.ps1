$ErrorActionPreference = "Stop"

git status
git add .
git commit -m "Checkpoint before Codex audit"
git push

git switch -c codex-audit
git status

Write-Host "Codex audit branch is ready."
