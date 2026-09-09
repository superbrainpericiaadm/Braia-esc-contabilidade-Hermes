#!/usr/bin/env bash

install_missing() {
  local source="$1" target="$2" mode="$3" owner="$4" group="$5"
  [[ -e "${target}" ]] && return 0
  install -D -m "${mode}" -o "${owner}" -g "${group}" "${source}" "${target}"
}

install_versioned() {
  local source="$1" target="$2" mode="$3" owner="$4" group="$5" backup_root="$6"
  if [[ -f "${target}" ]] && ! cmp -s -- "${source}" "${target}"; then
    local relative="${target#/}"
    install -D -m 0600 -o "${owner}" -g "${group}" "${target}" "${backup_root}/${relative}"
  fi
  install -D -m "${mode}" -o "${owner}" -g "${group}" "${source}" "${target}"
}

install_kit_artifacts() {
  local source_dir="$1" hermes_home="$2" owner="$3" group="$4" backup_root="$5"
  local workspace="$6"
  local agent

  install -d -m 0750 -o "${owner}" -g "${group}" \
    "${hermes_home}/agents" "${hermes_home}/skills" "${hermes_home}/scripts" \
    "${hermes_home}/skills/braia-claude-login/scripts" "${backup_root}"

  # Identidade, configuração e credenciais locais nunca são substituídas.
  if [[ ! -e "${hermes_home}/config.yaml" ]]; then
    local config_tmp
    config_tmp="$(mktemp)"
    sed "s|__WORKSPACE__|${workspace}|g" "${source_dir}/templates/config.yaml" > "${config_tmp}"
    install -m 0600 -o "${owner}" -g "${group}" "${config_tmp}" "${hermes_home}/config.yaml"
    rm -f -- "${config_tmp}"
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
