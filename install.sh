#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly SERVICE_USER="hermes-contadoria"
readonly SERVICE_GROUP="hermes-contadoria"
readonly SERVICE_HOME="/home/hermes-contadoria"
readonly HERMES_HOME="${SERVICE_HOME}/.hermes"
readonly WORKSPACE_DIR="${SERVICE_HOME}/workspace"
readonly RUNTIME_DIR="/opt/hermes-contadoria-runtime"
readonly KIT_MARKER="${HERMES_HOME}/.contadoria-kit-installed"
readonly KIT_IN_PROGRESS_MARKER="${HERMES_HOME}/.contadoria-kit-installing"
readonly HERMES_COMMIT="${HERMES_COMMIT:-29112bef099274229cadff79cdff7bf7b99c4b77}"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER_TMP=""

cleanup() {
  if [[ -n "${INSTALLER_TMP}" && -f "${INSTALLER_TMP}" ]]; then
    rm -f -- "${INSTALLER_TMP}"
  fi
}
trap cleanup EXIT

fail() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "execute como root: sudo bash install.sh"
[[ -r /etc/os-release ]] || fail "não foi possível identificar o sistema operacional"

# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "este kit suporta VPS Ubuntu 22.04 ou mais recente"
version_major="${VERSION_ID%%.*}"
[[ "${version_major}" =~ ^[0-9]+$ ]] || fail "versão do Ubuntu inválida"
(( version_major >= 22 )) || fail "é necessário Ubuntu 22.04 ou mais recente"

for required in \
  "${SOURCE_DIR}/templates/SOUL.md" \
  "${SOURCE_DIR}/templates/config.yaml" \
  "${SOURCE_DIR}/templates/profile.yaml" \
  "${SOURCE_DIR}/templates/hermes-contadoria.service.tpl" \
  "${SOURCE_DIR}/AGENTS.md" \
  "${SOURCE_DIR}/scripts/apply_runtime_patch.py" \
  "${SOURCE_DIR}/scripts/configure_multi_ai.py" \
  "${SOURCE_DIR}/runtime-patches/delegation-fallback.patch" \
  "${SOURCE_DIR}/runtime-patches/previous-delegation-fallback.patch" \
  "${SOURCE_DIR}/skills/braia-claude-login/SKILL.md" \
  "${SOURCE_DIR}/.env.example"; do
  [[ -f "${required}" ]] || fail "arquivo obrigatório ausente: ${required}"
done

if [[ -d "${HERMES_HOME}" && ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  existing_item="$(find "${HERMES_HOME}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)"
  [[ -z "${existing_item}" ]] || fail "${HERMES_HOME} já contém outra instalação; nada foi sobrescrito"
fi

if [[ -d "${RUNTIME_DIR}" && ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  fail "${RUNTIME_DIR} já existe sem o marcador deste kit; nada foi sobrescrito"
fi

printf '>> Preparando dependências básicas...\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git sudo

if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd --system "${SERVICE_GROUP}"
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "${SERVICE_HOME}" \
    --gid "${SERVICE_GROUP}" --shell /bin/bash "${SERVICE_USER}"
fi

install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
  "${HERMES_HOME}" "${WORKSPACE_DIR}"

if [[ ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  printf '%s\n' \
    "Instalação do kit Comunidade ContadorIA em andamento." \
    "Hermes commit: ${HERMES_COMMIT}" > "${KIT_IN_PROGRESS_MARKER}"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${KIT_IN_PROGRESS_MARKER}"
  chmod 0644 "${KIT_IN_PROGRESS_MARKER}"
fi

printf '>> Baixando o instalador oficial do Hermes no commit fixado %s...\n' "${HERMES_COMMIT}"
INSTALLER_TMP="$(mktemp /tmp/hermes-contadoria-install.XXXXXX)"
curl --fail --silent --show-error --location \
  --proto '=https' --tlsv1.2 \
  "https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_COMMIT}/scripts/install.sh" \
  --output "${INSTALLER_TMP}"

grep -q -- '--no-skills' "${INSTALLER_TMP}" || fail "o instalador oficial baixado não oferece --no-skills"

printf '>> Instalando Hermes sem o catálogo público de skills...\n'
HERMES_HOME="${HERMES_HOME}" HERMES_INSTALL_DIR="${RUNTIME_DIR}" \
  bash "${INSTALLER_TMP}" \
    --dir "${RUNTIME_DIR}" \
    --hermes-home "${HERMES_HOME}" \
    --branch main \
    --commit "${HERMES_COMMIT}" \
    --no-skills \
    --skip-setup \
    --non-interactive

printf '>> Aplicando extensão revisada de roteamento por tarefa...\n'
install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
  "${HERMES_HOME}/backups/runtime"
"${RUNTIME_DIR}/venv/bin/python" "${SOURCE_DIR}/scripts/apply_runtime_patch.py" \
  --runtime "${RUNTIME_DIR}" \
  --backup-root "${HERMES_HOME}/backups/runtime"

# O instalador roda como root. Torna o checkout/venv executável pelo grupo
# isolado do serviço sem abrir o runtime para outros usuários do sistema.
chown -R "root:${SERVICE_GROUP}" "${RUNTIME_DIR}"
chmod -R g+rX,o-rwx "${RUNTIME_DIR}"

first_kit_install=true
if [[ -f "${KIT_MARKER}" ]]; then
  first_kit_install=false
fi

if [[ "${first_kit_install}" == true ]]; then
  printf '>> Aplicando a configuração pública da Comunidade ContadorIA...\n'

  config_tmp="$(mktemp /tmp/hermes-contadoria-config.XXXXXX)"
  sed "s|__WORKSPACE__|${WORKSPACE_DIR}|g" \
    "${SOURCE_DIR}/templates/config.yaml" > "${config_tmp}"

  install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${config_tmp}" "${HERMES_HOME}/config.yaml"
  rm -f -- "${config_tmp}"

  install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${SOURCE_DIR}/.env.example" "${HERMES_HOME}/.env"
  install -m 0644 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${SOURCE_DIR}/templates/SOUL.md" "${HERMES_HOME}/SOUL.md"
  install -m 0644 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${SOURCE_DIR}/templates/profile.yaml" "${HERMES_HOME}/profile.yaml"
  install -m 0644 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${SOURCE_DIR}/AGENTS.md" "${HERMES_HOME}/AGENTS.md"

  install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${HERMES_HOME}/agents" "${HERMES_HOME}/skills" "${HERMES_HOME}/scripts"
  find "${SOURCE_DIR}/agents" -maxdepth 1 -type f -name '*.md' -print0 | \
    xargs -0 -I{} install -m 0644 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
      "{}" "${HERMES_HOME}/agents/"

  install -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${SOURCE_DIR}/scripts/configure_multi_ai.py" "${HERMES_HOME}/scripts/configure_multi_ai.py"
  install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${HERMES_HOME}/skills/braia-claude-login/scripts"
  install -m 0644 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${SOURCE_DIR}/skills/braia-claude-login/SKILL.md" \
    "${HERMES_HOME}/skills/braia-claude-login/SKILL.md"
  install -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${SOURCE_DIR}/skills/braia-claude-login/scripts/claude_login.py" \
    "${HERMES_HOME}/skills/braia-claude-login/scripts/claude_login.py"

  printf '%s\n' \
    "Comunidade ContadorIA kit" \
    "Hermes commit: ${HERMES_COMMIT}" > "${KIT_MARKER}"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${KIT_MARKER}"
  chmod 0644 "${KIT_MARKER}"
  rm -f -- "${KIT_IN_PROGRESS_MARKER}"
else
  printf '>> Kit já instalado; preservando SOUL.md, agentes, configuração e credenciais locais.\n'
fi

[[ -f "${HERMES_HOME}/.no-bundled-skills" ]] || \
  fail "o marcador .no-bundled-skills não foi criado"

[[ -f "${HERMES_HOME}/skills/braia-claude-login/SKILL.md" ]] || \
  fail "skill operacional de login Claude não foi instalada"

install -m 0644 "${SOURCE_DIR}/templates/hermes-contadoria.service.tpl" \
  /etc/systemd/system/hermes-contadoria.service
systemctl daemon-reload

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${HERMES_HOME}" "${WORKSPACE_DIR}"
chmod 0700 "${HERMES_HOME}"
chmod 0600 "${HERMES_HOME}/.env"

printf '\nInstalação-base concluída. O serviço ainda não foi iniciado.\n'
printf 'Próximo passo: sudo bash configure.sh\n'
