$ErrorActionPreference = "Stop"

if (Test-Path ".git") {
  git pull --ff-only
}

docker compose up -d --build
