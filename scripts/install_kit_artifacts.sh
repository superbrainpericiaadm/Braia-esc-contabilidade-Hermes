#!/usr/bin/env bash

KIT_ARTIFACT_TRANSACTION_ACTIVE=false
KIT_ARTIFACT_BACKUP_ROOT=""
declare -a KIT_ARTIFACT_TARGETS=()
declare -a KIT_ARTIFACT_BACKUPS=()
declare -a KIT_ARTIFACT_EXISTED=()
declare -a KIT_ARTIFACT_TEMPORARIES=()
FRESH_HOME_TRANSACTION_ACTIVE=false
FRESH_HOME_TRANSACTION_PATH=""
FRESH_HOME_TRANSACTION_ROOT=""
FRESH_HOME_PREVIOUS_PATH=""
FRESH_HOME_PREVIOUS_EXISTED=false
FRESH_HOME_RENAME_COMPLETED=false
FRESH_HOME_ARCHIVE_PATH=""
SAFE_ARTIFACT_HELPER="${SAFE_ARTIFACT_HELPER:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/secure_artifact.py}"

safe_artifact() {
  python3 "${SAFE_ARTIFACT_HELPER}" "$@"
}

begin_fresh_home_transaction() {
  local home="$1" backup_root="$2" staging_parent
  [[ "${FRESH_HOME_TRANSACTION_ACTIVE}" == false ]] || return 1
  [[ "${home}" == /* && ! -L "${home}" ]] || return 1
  [[ ! -e "${home}" || -d "${home}" ]] || return 1
  staging_parent="$(dirname "$(dirname "${home}")")" || return
  FRESH_HOME_TRANSACTION_ROOT="$(mktemp -d "${staging_parent}/.$(basename "${home}").transaction.XXXXXX")" || return
  chmod 0700 "${FRESH_HOME_TRANSACTION_ROOT}" || return
  FRESH_HOME_TRANSACTION_PATH="${home}"
  FRESH_HOME_ARCHIVE_PATH="${backup_root}/complete-home"
  FRESH_HOME_PREVIOUS_PATH="${FRESH_HOME_TRANSACTION_ROOT}/previous-home"
  FRESH_HOME_PREVIOUS_EXISTED=false
  FRESH_HOME_RENAME_COMPLETED=false
  if [[ -d "${home}" ]]; then
    FRESH_HOME_PREVIOUS_EXISTED=true
    FRESH_HOME_TRANSACTION_ACTIVE=true
    if ! mv -T -- "${home}" "${FRESH_HOME_PREVIOUS_PATH}"; then
      FRESH_HOME_TRANSACTION_ACTIVE=false
      return 1
    fi
    FRESH_HOME_RENAME_COMPLETED=true
  else
    FRESH_HOME_TRANSACTION_ACTIVE=true
  fi
  if [[ "${FRESH_HOME_PREVIOUS_EXISTED}" == true ]]; then
    cp -a --reflink=auto -- "${FRESH_HOME_PREVIOUS_PATH}" "${home}" || return
  fi
}

rollback_fresh_home_transaction() {
  local failed_root
  [[ "${FRESH_HOME_TRANSACTION_ACTIVE}" == true ]] || return 0
  [[ "${FRESH_HOME_TRANSACTION_PATH}" == /* && ! -L "${FRESH_HOME_TRANSACTION_PATH}" ]] || return 1
  if [[ "${FRESH_HOME_PREVIOUS_EXISTED}" == true && ! -e "${FRESH_HOME_PREVIOUS_PATH}" ]]; then
    if [[ "${FRESH_HOME_RENAME_COMPLETED}" == false && -d "${FRESH_HOME_TRANSACTION_PATH}" ]]; then
      FRESH_HOME_TRANSACTION_ACTIVE=false
      return 0
    fi
    return 1
  fi
  if [[ -e "${FRESH_HOME_TRANSACTION_PATH}" ]]; then
    [[ -d "${FRESH_HOME_TRANSACTION_PATH}" ]] || return 1
    failed_root="$(mktemp -d "${FRESH_HOME_TRANSACTION_ROOT}/failed-home.XXXXXX")" || return
    mv -T -- "${FRESH_HOME_TRANSACTION_PATH}" "${failed_root}/home" || return
  fi
  if [[ "${FRESH_HOME_PREVIOUS_EXISTED}" == true ]]; then
    [[ -d "${FRESH_HOME_PREVIOUS_PATH}" && ! -L "${FRESH_HOME_PREVIOUS_PATH}" ]] || return 1
    mv -T -- "${FRESH_HOME_PREVIOUS_PATH}" "${FRESH_HOME_TRANSACTION_PATH}" || return
  fi
  FRESH_HOME_TRANSACTION_ACTIVE=false
}

commit_fresh_home_transaction() {
  [[ "${FRESH_HOME_TRANSACTION_ACTIVE}" == true ]] || return 0
  [[ -d "${FRESH_HOME_TRANSACTION_PATH}" && ! -L "${FRESH_HOME_TRANSACTION_PATH}" ]] || return 1
  FRESH_HOME_TRANSACTION_ACTIVE=false
}

archive_home_transaction_snapshot() {
  [[ -d "${FRESH_HOME_TRANSACTION_ROOT}" && ! -L "${FRESH_HOME_TRANSACTION_ROOT}" ]] || return 1
  install -d -m 0700 "$(dirname "${FRESH_HOME_ARCHIVE_PATH}")" || return
  [[ ! -e "${FRESH_HOME_ARCHIVE_PATH}" && ! -L "${FRESH_HOME_ARCHIVE_PATH}" ]] || return 1
  mv -T -- "${FRESH_HOME_TRANSACTION_ROOT}" "${FRESH_HOME_ARCHIVE_PATH}" || return
}

assert_safe_artifact_target() {
  safe_artifact validate-file "$1"
}

assert_safe_artifact_directory() {
  safe_artifact validate-directory "$1"
}

begin_kit_artifact_transaction() {
  local backup_root="$1" owner="$2" group="$3"
  KIT_ARTIFACT_TARGETS=()
  KIT_ARTIFACT_BACKUPS=()
  KIT_ARTIFACT_EXISTED=()
  KIT_ARTIFACT_TEMPORARIES=()
  install -d -m 0700 -o "${owner}" -g "${group}" "$(dirname "${backup_root}")" || return
  KIT_ARTIFACT_BACKUP_ROOT="$(mktemp -d "${backup_root}.rollback.XXXXXX")" || return
  chown "${owner}:${group}" "${KIT_ARTIFACT_BACKUP_ROOT}" || return
  chmod 0700 "${KIT_ARTIFACT_BACKUP_ROOT}" || return
  KIT_ARTIFACT_TRANSACTION_ACTIVE=true
}

register_kit_artifact_target() {
  local target="$1" owner="$2" group="$3" index backup existing
  [[ "${KIT_ARTIFACT_TRANSACTION_ACTIVE}" == true ]] || return 0
  assert_safe_artifact_target "${target}" || return 1
  for existing in "${KIT_ARTIFACT_TARGETS[@]}"; do
    [[ "${existing}" == "${target}" ]] && return 0
  done
  index="${#KIT_ARTIFACT_TARGETS[@]}"
  backup="${KIT_ARTIFACT_BACKUP_ROOT}/${index}"
  if [[ -f "${target}" ]]; then
    safe_artifact copy "${target}" "${backup}" || return
    existing=true
  else
    existing=false
  fi
  KIT_ARTIFACT_TARGETS+=("${target}")
  KIT_ARTIFACT_BACKUPS+=("${backup}")
  KIT_ARTIFACT_EXISTED+=("${existing}")
}

atomic_install_artifact() {
  local source="$1" target="$2" mode="$3" owner="$4" group="$5" uid gid
  uid="$(id -u "${owner}")" || return
  gid="$(getent group "${group}" | cut -d: -f3)" || return
  safe_artifact copy "${source}" "${target}" --mode "${mode}" --uid "${uid}" --gid "${gid}"
}

atomic_restore_artifact() {
  safe_artifact copy "$1" "$2"
}

cleanup_kit_artifact_temporaries() {
  local temporary failed=false
  for temporary in "${KIT_ARTIFACT_TEMPORARIES[@]}"; do
    if [[ -f "${temporary}" || -L "${temporary}" ]]; then
      unlink -- "${temporary}" || failed=true
    fi
  done
  [[ "${failed}" == false ]]
}

rollback_kit_artifacts() {
  local index target backup failed=false
  [[ "${KIT_ARTIFACT_TRANSACTION_ACTIVE}" == true ]] || return 0
  for ((index=${#KIT_ARTIFACT_TARGETS[@]} - 1; index >= 0; index--)); do
    target="${KIT_ARTIFACT_TARGETS[index]}"
    backup="${KIT_ARTIFACT_BACKUPS[index]}"
    if [[ "${KIT_ARTIFACT_EXISTED[index]}" == true ]]; then
      atomic_restore_artifact "${backup}" "${target}" || failed=true
    else
      safe_artifact remove "${target}" || failed=true
    fi
  done
  cleanup_kit_artifact_temporaries || failed=true
  [[ "${failed}" == false ]] || return 1
  KIT_ARTIFACT_TRANSACTION_ACTIVE=false
}

commit_kit_artifact_transaction() {
  cleanup_kit_artifact_temporaries || return 1
  KIT_ARTIFACT_TRANSACTION_ACTIVE=false
}

install_missing() {
  local source="$1" target="$2" mode="$3" owner="$4" group="$5"
  assert_safe_artifact_target "${target}" || return
  [[ -e "${target}" ]] && return 0
  register_kit_artifact_target "${target}" "${owner}" "${group}" || return
  atomic_install_artifact "${source}" "${target}" "${mode}" "${owner}" "${group}" || return
}

install_versioned() {
  local source="$1" target="$2" mode="$3" owner="$4" group="$5" backup_root="$6"
  register_kit_artifact_target "${target}" "${owner}" "${group}" || return
  if [[ -f "${target}" ]] && ! cmp -s -- "${source}" "${target}"; then
    local relative="${target#/}"
    install -D -m 0600 -o "${owner}" -g "${group}" "${target}" "${backup_root}/${relative}" || return
  fi
  atomic_install_artifact "${source}" "${target}" "${mode}" "${owner}" "${group}" || return
}

install_kit_artifacts() {
  local source_dir="$1" hermes_home="$2" owner="$3" group="$4" backup_root="$5"
  local workspace="$6"
  local agent directory

  for directory in \
    "${hermes_home}/agents" "${hermes_home}/skills" "${hermes_home}/scripts" \
    "${hermes_home}/skills/braia-claude-login" \
    "${hermes_home}/skills/braia-claude-login/scripts" "${backup_root}"; do
    assert_safe_artifact_directory "${directory}" || return
  done

  install -d -m 0750 -o "${owner}" -g "${group}" \
    "${hermes_home}/agents" "${hermes_home}/skills" "${hermes_home}/scripts" \
    "${hermes_home}/skills/braia-claude-login/scripts" "${backup_root}" || return

  # Identidade, configuração e credenciais locais nunca são substituídas.
  assert_safe_artifact_target "${hermes_home}/config.yaml" || return
  if [[ ! -e "${hermes_home}/config.yaml" ]]; then
    local config_tmp
    config_tmp="$(mktemp)" || return
    sed "s|__WORKSPACE__|${workspace}|g" "${source_dir}/templates/config.yaml" > "${config_tmp}" || return
    install_missing "${config_tmp}" "${hermes_home}/config.yaml" 0600 "${owner}" "${group}" || return
    unlink -- "${config_tmp}" || return
  fi
  install_missing "${source_dir}/.env.example" "${hermes_home}/.env" 0600 "${owner}" "${group}" || return
  install_missing "${source_dir}/templates/SOUL.md" "${hermes_home}/SOUL.md" 0644 "${owner}" "${group}" || return
  install_missing "${source_dir}/templates/profile.yaml" "${hermes_home}/profile.yaml" 0644 "${owner}" "${group}" || return
  install_missing "${source_dir}/AGENTS.md" "${hermes_home}/AGENTS.md" 0644 "${owner}" "${group}" || return
  while IFS= read -r -d '' agent; do
    install_missing "${agent}" "${hermes_home}/agents/$(basename "${agent}")" 0644 "${owner}" "${group}" || return
  done < <(find "${source_dir}/agents" -maxdepth 1 -type f -name '*.md' -print0)

  # Código operacional versionado é atualizado, com cópia privada do byte anterior.
  install_versioned "${source_dir}/scripts/configure_multi_ai.py" \
    "${hermes_home}/scripts/configure_multi_ai.py" 0750 "${owner}" "${group}" "${backup_root}" || return
  install_versioned "${source_dir}/skills/braia-claude-login/SKILL.md" \
    "${hermes_home}/skills/braia-claude-login/SKILL.md" 0644 "${owner}" "${group}" "${backup_root}" || return
  install_versioned "${source_dir}/skills/braia-claude-login/scripts/claude_login.py" \
    "${hermes_home}/skills/braia-claude-login/scripts/claude_login.py" 0750 "${owner}" "${group}" "${backup_root}" || return
}
