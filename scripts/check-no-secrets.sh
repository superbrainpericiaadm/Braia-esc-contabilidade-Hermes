#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

failed=false

if find . -type d \( -name skills -o -name memories -o -name sessions -o -name credentials -o -name secrets \) -print -quit | grep -q .; then
  printf 'ERRO: diretório proibido encontrado no pacote público.\n' >&2
  failed=true
fi

if find . -type f \( -name auth.json -o -name '*.db' -o -name '*.db-*' -o -name '*.pem' -o -name '*.key' \) -print -quit | grep -q .; then
  printf 'ERRO: arquivo sensível/estado proibido encontrado no pacote público.\n' >&2
  failed=true
fi

secret_pattern='(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-(proj-)?[A-Za-z0-9_-]{24,}|xox[baprs]-[A-Za-z0-9-]{20,}|[0-9]{6,15}:[A-Za-z0-9_-]{30,})'
if rg -n --hidden --glob '!.git/**' --glob '!.env.example' \
  --glob '!scripts/check-no-secrets.sh' \
  "${secret_pattern}" .; then
  printf 'ERRO: possível segredo encontrado.\n' >&2
  failed=true
fi

if rg -n --hidden --glob '!.git/**' --glob '!scripts/check-no-secrets.sh' \
  'braia-oauth-client|client_secret[[:space:]]*[:=][[:space:]]*[^[:space:]]+' .; then
  printf 'ERRO: referência a credencial privada encontrada.\n' >&2
  failed=true
fi

if [[ "${failed}" == true ]]; then
  exit 1
fi

printf 'OK: nenhum diretório de skills/estado nem padrão conhecido de segredo foi encontrado.\n'
