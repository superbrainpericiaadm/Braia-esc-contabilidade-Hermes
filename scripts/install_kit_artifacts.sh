#!/usr/bin/env bash

KIT_ARTIFACT_TRANSACTION_ACTIVE=false
KIT_ARTIFACT_BACKUP_ROOT=""
declare -a KIT_ARTIFACT_TARGETS=()
declare -a KIT_ARTIFACT_BACKUPS=()
declare -a KIT_ARTIFACT_EXISTED=()
declare -a KIT_ARTIFACT_TEMPORARIES=()

assert_safe_artifact_target() {
  local target="$1" current="/" part
  [[ "${target}" == /* ]] || return 1
  IFS='/' read -r -a parts <<< "${target#/}"
  for part in "${parts[@]:0:${#parts[@]}-1}"; do
    [[ -n "${part}" ]] || continue
    current="${current%/}/${part}"
    [[ ! -L "${current}" ]] || return 1
  done
  [[ ! -L "${target}" ]] || return 1
  [[ ! -e "${target}" || -f "${target}" ]] || return 1
}

begin_kit_artifact_transaction() {
  local backup_root="$1" owner="$2" group="$3"
  KIT_ARTIFACT_TARGETS=()
  KIT_ARTIFACT_BACKUPS=()
  KIT_ARTIFACT_EXISTED=()
  KIT_ARTIFACT_TEMPORARIES=()
  install -d -m 0700 -o "${owner}" -g "${group}" "$(dirname "${backup_root}")"
  KIT_ARTIFACT_BACKUP_ROOT="$(mktemp -d "${backup_root}.rollback.XXXXXX")"
  chown "${owner}:${group}" "${KIT_ARTIFACT_BACKUP_ROOT}"
  chmod 0700 "${KIT_ARTIFACT_BACKUP_ROOT}"
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
    cp -p -- "${target}" "${backup}"
    KIT_ARTIFACT_EXISTED+=(true)
  else
    KIT_ARTIFACT_EXISTED+=(false)
  fi
  KIT_ARTIFACT_TARGETS+=("${target}")
  KIT_ARTIFACT_BACKUPS+=("${backup}")
}

atomic_install_artifact() {
  local source="$1" target="$2" mode="$3" owner="$4" group="$5" temporary
  assert_safe_artifact_target "${target}" || return 1
  temporary="$(mktemp --tmpdir="$(dirname "${target}")" ".$(basename "${target}").new.XXXXXX")"
  KIT_ARTIFACT_TEMPORARIES+=("${temporary}")
  install -m "${mode}" -o "${owner}" -g "${group}" "${source}" "${temporary}"
  mv -fT -- "${temporary}" "${target}"
}

atomic_restore_artifact() {
  local source="$1" target="$2" temporary
  assert_safe_artifact_target "${target}" || return 1
  temporary="$(mktemp --tmpdir="$(dirname "${target}")" ".$(basename "${target}").rollback.XXXXXX")"
  KIT_ARTIFACT_TEMPORARIES+=("${temporary}")
  cp -p -- "${source}" "${temporary}"
  mv -fT -- "${temporary}" "${target}"
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
    elif [[ -f "${target}" || -L "${target}" ]]; then
      unlink -- "${target}" || failed=true
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
  [[ -e "${target}" ]] && return 0
  register_kit_artifact_target "${target}" "${owner}" "${group}"
  atomic_install_artifact "${source}" "${target}" "${mode}" "${owner}" "${group}"
}

install_versioned() {
  local source="$1" target="$2" mode="$3" owner="$4" group="$5" backup_root="$6"
  register_kit_artifact_target "${target}" "${owner}" "${group}"
  if [[ -f "${target}" ]] && ! cmp -s -- "${source}" "${target}"; then
    local relative="${target#/}"
    install -D -m 0600 -o "${owner}" -g "${group}" "${target}" "${backup_root}/${relative}"
  fi
  atomic_install_artifact "${source}" "${target}" "${mode}" "${owner}" "${group}"
}

install_kit_artifacts() {
  local source_dir="$1" hermes_home="$2" owner="$3" group="$4" backup_root="$5"
  local workspace="$6"
  local agent directory

  for directory in \
    "${hermes_home}/agents" "${hermes_home}/skills" "${hermes_home}/scripts" \
    "${hermes_home}/skills/braia-claude-login/scripts" "${backup_root}"; do
    assert_safe_artifact_target "${directory}/.transaction-guard" || return 1
  done
  install -d -m 0750 -o "${owner}" -g "${group}" \
    "${hermes_home}/agents" "${hermes_home}/skills" "${hermes_home}/scripts" \
    "${hermes_home}/skills/braia-claude-login/scripts" "${backup_root}"

  # Identidade, configuração e credenciais locais nunca são substituídas.
  if [[ ! -e "${hermes_home}/config.yaml" ]]; then
    local config_tmp
    config_tmp="$(mktemp)"
    sed "s|__WORKSPACE__|${workspace}|g" "${source_dir}/templates/config.yaml" > "${config_tmp}"
    install_missing "${config_tmp}" "${hermes_home}/config.yaml" 0600 "${owner}" "${group}"
    unlink -- "${config_tmp}"
  fi
  install_missing "${source_dir}/.env.example" "${hermes_home}/.env" 0600 "${owner}" "${group}"
  install_missing "${source_dir}/templates/SOUL.md" "${hermes_home}/SOUL.md" 0644 "${owner}" "${group}"
  install_missing "${source_dir}/templates/profile.yaml" "${hermes_home}/profile.yaml" 0644 "${owner}" "${group}"
  install_missing "${source_dir}/AGENTS.md" "${hermes_home}/AGENTS.md" 0644 "${owner}" "${group}"
  while IFS= read -r -d '' agent; do
    install_missing "${agent}" "${hermes_home}/agents/$(basename "${agent}")" 0644 "${owner}" "${group}"
  done < <(find "${source_dir}/agents" -maxdepth 1 -type f -name '*.md' -print0)

  # Código operacional versionado é atualizado, com cópia privada do byte anterior.
  install_versioned "${source_dir}/scripts/configure_multi_ai.py" \
    "${hermes_home}/scripts/configure_multi_ai.py" 0750 "${owner}" "${group}" "${backup_root}"
  install_versioned "${source_dir}/skills/braia-claude-login/SKILL.md" \
    "${hermes_home}/skills/braia-claude-login/SKILL.md" 0644 "${owner}" "${group}" "${backup_root}"
  install_versioned "${source_dir}/skills/braia-claude-login/scripts/claude_login.py" \
    "${hermes_home}/skills/braia-claude-login/scripts/claude_login.py" 0750 "${owner}" "${group}" "${backup_root}"
}
