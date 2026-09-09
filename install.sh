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
INSTALL_SUCCEEDED=false
SERVICE_UNIT_TRANSACTIONAL=false
HERMES_HOME_LOCKED=false

cleanup() {
  local status=$?
  local recovery_ok=true
  trap - EXIT
  if [[ -n "${INSTALLER_TMP}" && -f "${INSTALLER_TMP}" ]]; then
    if ! unlink -- "${INSTALLER_TMP}"; then
      printf 'ATENÇÃO: não foi possível retirar o instalador temporário.\n' >&2
      status=1
    fi
  fi
  if [[ "${INSTALL_SUCCEEDED}" != true && "${SERVICE_STATE_CAPTURED:-false}" == true ]]; then
    if ! quiesce_service_for_recovery; then
      printf 'ERRO: não foi possível manter o serviço parado durante a recuperação.\n' >&2
      recovery_ok=false
      status=1
    fi
    if [[ "${recovery_ok}" == true ]]; then
      if ! rollback_runtime_transaction "${RUNTIME_DIR}"; then
        printf 'ERRO: não foi possível reverter o runtime.\n' >&2
        recovery_ok=false
        status=1
      fi
      if ! rollback_kit_artifacts; then
        printf 'ERRO: não foi possível reverter os artefatos do kit.\n' >&2
        recovery_ok=false
        status=1
      fi
      if [[ "${SERVICE_UNIT_TRANSACTIONAL}" == true ]] && ! systemctl daemon-reload; then
        printf 'ERRO: não foi possível recarregar a unidade restaurada.\n' >&2
        recovery_ok=false
        status=1
      fi
      if [[ "${HERMES_HOME_LOCKED}" == true ]]; then
        if ! chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${HERMES_HOME}" "${WORKSPACE_DIR}"; then
          printf 'ERRO: não foi possível restaurar a propriedade da instalação.\n' >&2
          recovery_ok=false
          status=1
        elif ! chmod 0700 "${HERMES_HOME}"; then
          recovery_ok=false
          status=1
        else
          HERMES_HOME_LOCKED=false
        fi
      fi
    fi
    if [[ "${recovery_ok}" == true ]] && ! restore_service_state; then
      printf 'ERRO: não foi possível restaurar o estado anterior do serviço.\n' >&2
      status=1
    elif [[ "${recovery_ok}" != true ]]; then
      printf 'ERRO: serviço mantido parado para não observar estado parcialmente recuperado.\n' >&2
    fi
  fi
  exit "${status}"
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
  "${SOURCE_DIR}/scripts/runtime_transaction.sh" \
  "${SOURCE_DIR}/scripts/service_transaction.sh" \
  "${SOURCE_DIR}/runtime-patches/delegation-fallback.patch" \
  "${SOURCE_DIR}/runtime-patches/previous-delegation-fallback.patch" \
  "${SOURCE_DIR}/skills/braia-claude-login/SKILL.md" \
  "${SOURCE_DIR}/skills/braia-claude-login/scripts/claude_login.py" \
  "${SOURCE_DIR}/.env.example"; do
  [[ -f "${required}" ]] || fail "arquivo obrigatório ausente: ${required}"
done
# shellcheck disable=SC1091
source "${SOURCE_DIR}/scripts/install_kit_artifacts.sh"
# shellcheck disable=SC1091
source "${SOURCE_DIR}/scripts/runtime_transaction.sh"
# shellcheck disable=SC1091
source "${SOURCE_DIR}/scripts/service_transaction.sh"

if [[ -d "${HERMES_HOME}" && ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  existing_item="$(find "${HERMES_HOME}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)"
  [[ -z "${existing_item}" ]] || fail "${HERMES_HOME} já contém outra instalação; nada foi sobrescrito"
fi
if [[ -d "${RUNTIME_DIR}" && ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  fail "${RUNTIME_DIR} já existe sem o marcador deste kit; nada foi sobrescrito"
fi
if [[ -d "${HERMES_HOME}" ]]; then
  unexpected_link="$(find "${HERMES_HOME}" -type l -print -quit 2>/dev/null || true)"
  [[ -z "${unexpected_link}" ]] || fail "link simbólico não permitido na instalação: ${unexpected_link}"
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

capture_service_state
stop_service_for_update
HERMES_HOME_LOCKED=true
chown -R root:root "${HERMES_HOME}"
chmod 0700 "${HERMES_HOME}"
begin_runtime_transaction "${RUNTIME_DIR}" /opt/.hermes-contadoria-runtime-transactions root root

artifact_backup="/var/backups/hermes-contadoria/kit-artifacts/$(date -u +%Y%m%dT%H%M%SZ)"
begin_kit_artifact_transaction "${artifact_backup}" root root
register_kit_artifact_target "${KIT_MARKER}" "${SERVICE_USER}" "${SERVICE_GROUP}"
# O marcador de retomada fica fora do rollback para permitir repetição após falha inicial.

if [[ ! -f "${KIT_MARKER}" && ! -f "${KIT_IN_PROGRESS_MARKER}" ]]; then
  marker_tmp="$(mktemp)"
  printf '%s\n' "Instalação do kit Comunidade ContadorIA em andamento." "Hermes commit: ${HERMES_COMMIT}" > "${marker_tmp}"
  atomic_install_artifact "${marker_tmp}" "${KIT_IN_PROGRESS_MARKER}" 0644 "${SERVICE_USER}" "${SERVICE_GROUP}"
  unlink -- "${marker_tmp}"
fi

install_kit_artifacts "${SOURCE_DIR}" "${HERMES_HOME}" "${SERVICE_USER}" "${SERVICE_GROUP}" "${artifact_backup}" "${WORKSPACE_DIR}"

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
  marker_tmp="$(mktemp)"
  printf '%s\n' "Comunidade ContadorIA kit" "Hermes commit: ${HERMES_COMMIT}" > "${marker_tmp}"
  atomic_install_artifact "${marker_tmp}" "${KIT_MARKER}" 0644 "${SERVICE_USER}" "${SERVICE_GROUP}"
  unlink -- "${marker_tmp}"
else
  printf '>> Kit atualizado; identidade, agentes, configuração e credenciais locais foram preservados.\n'
fi

[[ -f "${HERMES_HOME}/.no-bundled-skills" ]] || fail "o marcador .no-bundled-skills não foi criado"
[[ -f "${HERMES_HOME}/skills/braia-claude-login/SKILL.md" ]] || fail "skill operacional de login Claude não foi instalada"
[[ -x "${HERMES_HOME}/scripts/configure_multi_ai.py" ]] || fail "configurador multi-IA não foi instalado"
register_kit_artifact_target "/etc/systemd/system/hermes-contadoria.service" root root
SERVICE_UNIT_TRANSACTIONAL=true
atomic_install_artifact "${SOURCE_DIR}/templates/hermes-contadoria.service.tpl" \
  /etc/systemd/system/hermes-contadoria.service 0644 root root
systemctl daemon-reload
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${HERMES_HOME}" "${WORKSPACE_DIR}"
chmod 0700 "${HERMES_HOME}"
chmod 0600 "${HERMES_HOME}/.env"
restore_service_state || fail "serviço não voltou ao estado anterior"
commit_kit_artifact_transaction
commit_runtime_transaction
[[ ! -f "${KIT_IN_PROGRESS_MARKER}" ]] || unlink -- "${KIT_IN_PROGRESS_MARKER}"
INSTALL_SUCCEEDED=true

printf '\nInstalação-base concluída. Serviço anterior restaurado, se aplicável.\n'
printf 'Próximo passo para instalação nova: sudo bash configure.sh\n'
