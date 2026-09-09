#!/usr/bin/env bash

RUNTIME_TRANSACTION_ACTIVE=false
RUNTIME_PREVIOUSLY_PRESENT=false
RUNTIME_TRANSACTION_DIR=""
RUNTIME_ROLLBACK_DIR=""
RUNTIME_FAILED_DIR=""

begin_runtime_transaction() {
  local runtime="$1" transaction_dir="$2" owner="$3" group="$4" stamp
  [[ "${runtime}" == /* && "${transaction_dir}" == /* && ! -L "${runtime}" && ! -L "${transaction_dir}" ]] || return 1
  RUNTIME_TRANSACTION_DIR="${transaction_dir}"
  install -d -m 0700 -o "${owner}" -g "${group}" "${RUNTIME_TRANSACTION_DIR}"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  RUNTIME_ROLLBACK_DIR="${RUNTIME_TRANSACTION_DIR}/runtime-${stamp}"
  RUNTIME_FAILED_DIR="${RUNTIME_TRANSACTION_DIR}/failed-${stamp}"
  if [[ -d "${runtime}" ]]; then
    RUNTIME_PREVIOUSLY_PRESENT=true
    mv -T -- "${runtime}" "${RUNTIME_ROLLBACK_DIR}"
    RUNTIME_TRANSACTION_ACTIVE=true
    cp -a -- "${RUNTIME_ROLLBACK_DIR}" "${runtime}"
  else
    RUNTIME_PREVIOUSLY_PRESENT=false
    RUNTIME_TRANSACTION_ACTIVE=true
  fi
}

rollback_runtime_transaction() {
  local runtime="$1"
  [[ "${RUNTIME_TRANSACTION_ACTIVE}" == true ]] || return 0
  [[ "${runtime}" == /* && ! -L "${runtime}" ]] || return 1
  if [[ -e "${runtime}" ]]; then
    mv -T -- "${runtime}" "${RUNTIME_FAILED_DIR}" || return 1
  fi
  if [[ "${RUNTIME_PREVIOUSLY_PRESENT}" == true ]]; then
    mv -T -- "${RUNTIME_ROLLBACK_DIR}" "${runtime}" || return 1
  fi
  RUNTIME_TRANSACTION_ACTIVE=false
}

commit_runtime_transaction() {
  RUNTIME_TRANSACTION_ACTIVE=false
}
