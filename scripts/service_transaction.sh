#!/usr/bin/env bash

SERVICE_NAME="${SERVICE_NAME:-hermes-contadoria.service}"
SERVICE_STATE_CAPTURED=false
SERVICE_PREVIOUS_STATE=inactive
SERVICE_STATE_RESTORED=false

capture_service_state() {
  local status
  if systemctl is-active --quiet "${SERVICE_NAME}"; then
    SERVICE_PREVIOUS_STATE=active
  else
    status=$?
    case "${status}" in
      3) SERVICE_PREVIOUS_STATE=inactive ;;
      4) SERVICE_PREVIOUS_STATE=absent ;;
      *) return "${status}" ;;
    esac
  fi
  SERVICE_STATE_CAPTURED=true
  SERVICE_STATE_RESTORED=false
}

stop_service_for_update() {
  [[ "${SERVICE_STATE_CAPTURED}" == true ]] || return 1
  if [[ "${SERVICE_PREVIOUS_STATE}" == active ]]; then
    printf '>> Parando temporariamente o serviço para atualização transacional...\n'
    systemctl stop "${SERVICE_NAME}"
  fi
}

quiesce_service_for_recovery() {
  local status
  if systemctl is-active --quiet "${SERVICE_NAME}"; then
    systemctl stop "${SERVICE_NAME}"
  else
    status=$?
    [[ "${status}" == 3 || "${status}" == 4 ]]
  fi
}

restore_service_state() {
  local status
  [[ "${SERVICE_STATE_CAPTURED}" == true ]] || return 0

  if [[ "${SERVICE_PREVIOUS_STATE}" == active ]]; then
    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
      systemctl start "${SERVICE_NAME}" || return 1
    fi
    systemctl is-active --quiet "${SERVICE_NAME}" || return 1
  else
    if systemctl is-active --quiet "${SERVICE_NAME}"; then
      systemctl stop "${SERVICE_NAME}" || return 1
    else
      status=$?
      [[ "${status}" == 3 || "${status}" == 4 ]] || return "${status}"
    fi
    if systemctl is-active --quiet "${SERVICE_NAME}"; then
      return 1
    else
      status=$?
      [[ "${status}" == 3 || "${status}" == 4 ]] || return "${status}"
    fi
  fi

  SERVICE_STATE_RESTORED=true
}
