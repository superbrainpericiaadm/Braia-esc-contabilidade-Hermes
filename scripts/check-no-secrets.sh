#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

failed=false

if git ls-files -co --exclude-standard | grep -Eq '(^|/)(memories|sessions|credentials|secrets)/'; then
  printf 'ERRO: diretório de estado ou credencial encontrado no pacote público.\n' >&2
  failed=true
fi

if git ls-files -co --exclude-standard | grep -Eq '(^|/)(auth\.json|[^/]+\.db(-[^/]*)?|[^/]+\.(pem|key))$'; then
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

printf 'OK: nenhum estado privado, credencial ou padrão conhecido de segredo foi encontrado.\n'
