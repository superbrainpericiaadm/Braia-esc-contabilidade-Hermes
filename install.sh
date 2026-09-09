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
SERVICE_WAS_ACTIVE=false
INSTALL_SUCCEEDED=false

cleanup() {
  if [[ -n "${INSTALLER_TMP}" && -f "${INSTALLER_TMP}" ]]; then
    rm -f -- "${INSTALLER_TMP}"
  fi
  if [[ "${SERVICE_WAS_ACTIVE}" == true && "${INSTALL_SUCCEEDED}" != true ]]; then
    printf 'ATENÇÃO: serviço permaneceu parado após falha de atualização; inspecione o runtime antes de iniciar.\n' >&2
  fi
}
trap cleanup EXIT

fail() { printf 'ERRO: %s\n' "$*" >&2; exit 1; }

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
  "${SOURCE_DIR}/scripts/install_kit_artifacts.sh" \
  "${SOURCE_DIR}/runtime-patches/delegation-fallback.patch" \
  "${SOURCE_DIR}/runtime-patches/previous-delegation-fallback.patch" \
  "${SOURCE_DIR}/skills/braia-claude-login/SKILL.md" \
  "${SOURCE_DIR}/skills/braia-claude-login/scripts/claude_login.py" \
  "${SOURCE_DIR}/.env.example"; do
  [[ -f "${required}" ]] || fail "arquivo obrigatório ausente: ${required}"
done
# shellcheck disable=SC1091
source "${SOURCE_DIR}/scripts/install_kit_artifacts.sh"

if [[ -d "${HERMES_HOME}" && ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  existing_item="$(find "${HERMES_HOME}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)"
  [[ -z "${existing_item}" ]] || fail "${HERMES_HOME} já contém outra instalação; nada foi sobrescrito"
fi
if [[ -d "${RUNTIME_DIR}" && ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  fail "${RUNTIME_DIR} já existe sem o marcador deste kit; nada foi sobrescrito"
fi

# Valide a atualização existente antes de apt, download, parada ou mutação do runtime.
if [[ -f "${KIT_MARKER}" ]]; then
  id "${SERVICE_USER}" >/dev/null 2>&1 || fail "marcador existe, mas usuário de serviço está ausente"
  [[ -x "${RUNTIME_DIR}/venv/bin/python" ]] || fail "marcador existe, mas runtime Hermes está incompleto"
  "${RUNTIME_DIR}/venv/bin/python" "${SOURCE_DIR}/scripts/apply_runtime_patch.py" \
    --runtime "${RUNTIME_DIR}" --backup-root "${HERMES_HOME}/backups/runtime" --check >/dev/null || \
    fail "runtime existente não passou na validação preservadora; nada foi alterado"
fi

printf '>> Preparando dependências básicas...\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git sudo
getent group "${SERVICE_GROUP}" >/dev/null || groupadd --system "${SERVICE_GROUP}"
id "${SERVICE_USER}" >/dev/null 2>&1 || useradd --system --create-home --home-dir "${SERVICE_HOME}" --gid "${SERVICE_GROUP}" --shell /bin/bash "${SERVICE_USER}"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${HERMES_HOME}" "${WORKSPACE_DIR}"

if [[ ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  printf '%s\n' "Instalação do kit Comunidade ContadorIA em andamento." "Hermes commit: ${HERMES_COMMIT}" > "${KIT_IN_PROGRESS_MARKER}"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${KIT_IN_PROGRESS_MARKER}"
  chmod 0644 "${KIT_IN_PROGRESS_MARKER}"
fi

artifact_backup="${HERMES_HOME}/backups/kit-artifacts/$(date -u +%Y%m%dT%H%M%SZ)"
install_kit_artifacts "${SOURCE_DIR}" "${HERMES_HOME}" "${SERVICE_USER}" "${SERVICE_GROUP}" "${artifact_backup}" "${WORKSPACE_DIR}"

if systemctl is-active --quiet hermes-contadoria.service; then
  SERVICE_WAS_ACTIVE=true
  printf '>> Parando temporariamente o serviço para atualizar o runtime de forma coerente...\n'
  systemctl stop hermes-contadoria.service
fi

printf '>> Baixando o instalador oficial do Hermes no commit fixado %s...\n' "${HERMES_COMMIT}"
INSTALLER_TMP="$(mktemp /tmp/hermes-contadoria-install.XXXXXX)"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_COMMIT}/scripts/install.sh" --output "${INSTALLER_TMP}"
grep -q -- '--no-skills' "${INSTALLER_TMP}" || fail "o instalador oficial baixado não oferece --no-skills"

printf '>> Instalando Hermes sem o catálogo público de skills...\n'
HERMES_HOME="${HERMES_HOME}" HERMES_INSTALL_DIR="${RUNTIME_DIR}" bash "${INSTALLER_TMP}" \
  --dir "${RUNTIME_DIR}" --hermes-home "${HERMES_HOME}" --branch main --commit "${HERMES_COMMIT}" \
  --no-skills --skip-setup --non-interactive

printf '>> Aplicando extensão revisada de roteamento por tarefa...\n'
install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${HERMES_HOME}/backups/runtime"
"${RUNTIME_DIR}/venv/bin/python" "${SOURCE_DIR}/scripts/apply_runtime_patch.py" \
  --runtime "${RUNTIME_DIR}" --backup-root "${HERMES_HOME}/backups/runtime"
chown -R "root:${SERVICE_GROUP}" "${RUNTIME_DIR}"
chmod -R g+rX,o-rwx "${RUNTIME_DIR}"

if [[ ! -f "${KIT_MARKER}" ]]; then
  printf '%s\n' "Comunidade ContadorIA kit" "Hermes commit: ${HERMES_COMMIT}" > "${KIT_MARKER}"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${KIT_MARKER}"
  chmod 0644 "${KIT_MARKER}"
  rm -f -- "${KIT_IN_PROGRESS_MARKER}"
else
  printf '>> Kit atualizado; identidade, agentes, configuração e credenciais locais foram preservados.\n'
fi

[[ -f "${HERMES_HOME}/.no-bundled-skills" ]] || fail "o marcador .no-bundled-skills não foi criado"
[[ -f "${HERMES_HOME}/skills/braia-claude-login/SKILL.md" ]] || fail "skill operacional de login Claude não foi instalada"
[[ -x "${HERMES_HOME}/scripts/configure_multi_ai.py" ]] || fail "configurador multi-IA não foi instalado"
install -m 0644 "${SOURCE_DIR}/templates/hermes-contadoria.service.tpl" /etc/systemd/system/hermes-contadoria.service
systemctl daemon-reload
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${HERMES_HOME}" "${WORKSPACE_DIR}"
chmod 0700 "${HERMES_HOME}"
chmod 0600 "${HERMES_HOME}/.env"
if [[ "${SERVICE_WAS_ACTIVE}" == true ]]; then
  systemctl start hermes-contadoria.service
  systemctl is-active --quiet hermes-contadoria.service || fail "serviço anterior não voltou ao estado ativo"
fi
INSTALL_SUCCEEDED=true

printf '\nInstalação-base concluída. Serviço anterior restaurado, se aplicável.\n'
printf 'Próximo passo para instalação nova: sudo bash configure.sh\n'
