#!/usr/bin/env bash

RUNTIME_TRANSACTION_ACTIVE=false
RUNTIME_TRANSACTION_PATH=""
RUNTIME_TRANSACTION_ROOT=""
RUNTIME_PREVIOUS_PATH=""
RUNTIME_PREVIOUS_EXISTED=false
RUNTIME_RENAME_COMPLETED=false
RUNTIME_ARCHIVE_PATH=""

assert_safe_runtime_path() {
  local runtime="$1" parent
  [[ "${runtime}" == /* ]] || return 1
  [[ ! -L "${runtime}" ]] || return 1
  [[ ! -e "${runtime}" || -d "${runtime}" ]] || return 1
  parent="$(dirname "${runtime}")" || return
  [[ -d "${parent}" && ! -L "${parent}" ]] || return 1
}

begin_runtime_transaction() {
  local runtime="$1" backup_root="$2" runtime_parent
  [[ "${RUNTIME_TRANSACTION_ACTIVE}" == false ]] || return 1
  assert_safe_runtime_path "${runtime}" || return
  runtime_parent="$(dirname "${runtime}")" || return
  RUNTIME_TRANSACTION_ROOT="$(mktemp -d "${runtime_parent}/.$(basename "${runtime}").transaction.XXXXXX")" || return
  chmod 0700 "${RUNTIME_TRANSACTION_ROOT}" || return
  RUNTIME_TRANSACTION_PATH="${runtime}"
  RUNTIME_ARCHIVE_PATH="${backup_root}/complete-runtime"
  RUNTIME_PREVIOUS_PATH="${RUNTIME_TRANSACTION_ROOT}/previous-runtime"
  RUNTIME_PREVIOUS_EXISTED=false
  RUNTIME_RENAME_COMPLETED=false
  if [[ -d "${runtime}" ]]; then
    RUNTIME_PREVIOUS_EXISTED=true
    RUNTIME_TRANSACTION_ACTIVE=true
    if ! mv -T -- "${runtime}" "${RUNTIME_PREVIOUS_PATH}"; then
      RUNTIME_TRANSACTION_ACTIVE=false
      return 1
    fi
    RUNTIME_RENAME_COMPLETED=true
  else
    RUNTIME_TRANSACTION_ACTIVE=true
  fi
}

rollback_runtime_transaction() {
  local failed_path
  [[ "${RUNTIME_TRANSACTION_ACTIVE}" == true ]] || return 0
  assert_safe_runtime_path "${RUNTIME_TRANSACTION_PATH}" || return
  if [[ "${RUNTIME_PREVIOUS_EXISTED}" == true && ! -e "${RUNTIME_PREVIOUS_PATH}" ]]; then
    if [[ "${RUNTIME_RENAME_COMPLETED}" == false && -d "${RUNTIME_TRANSACTION_PATH}" ]]; then
      RUNTIME_TRANSACTION_ACTIVE=false
      return 0
    fi
    return 1
  fi
  if [[ -e "${RUNTIME_TRANSACTION_PATH}" ]]; then
    failed_path="$(mktemp -d "${RUNTIME_TRANSACTION_ROOT}/failed-runtime.XXXXXX")" || return
    mv -T -- "${RUNTIME_TRANSACTION_PATH}" "${failed_path}/runtime" || return
  fi
  if [[ "${RUNTIME_PREVIOUS_EXISTED}" == true ]]; then
    [[ -d "${RUNTIME_PREVIOUS_PATH}" && ! -L "${RUNTIME_PREVIOUS_PATH}" ]] || return 1
    mv -T -- "${RUNTIME_PREVIOUS_PATH}" "${RUNTIME_TRANSACTION_PATH}" || return
  fi
  RUNTIME_TRANSACTION_ACTIVE=false
}

commit_runtime_transaction() {
  [[ "${RUNTIME_TRANSACTION_ACTIVE}" == true ]] || return 0
  [[ -d "${RUNTIME_TRANSACTION_PATH}" && ! -L "${RUNTIME_TRANSACTION_PATH}" ]] || return 1
  RUNTIME_TRANSACTION_ACTIVE=false
}

archive_runtime_transaction_snapshot() {
  [[ -d "${RUNTIME_TRANSACTION_ROOT}" && ! -L "${RUNTIME_TRANSACTION_ROOT}" ]] || return 1
  install -d -m 0700 "$(dirname "${RUNTIME_ARCHIVE_PATH}")" || return
  [[ ! -e "${RUNTIME_ARCHIVE_PATH}" && ! -L "${RUNTIME_ARCHIVE_PATH}" ]] || return 1
  mv -T -- "${RUNTIME_TRANSACTION_ROOT}" "${RUNTIME_ARCHIVE_PATH}" || return
}
