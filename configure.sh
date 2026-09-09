#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly SERVICE_USER="hermes-contadoria"
readonly SERVICE_GROUP="hermes-contadoria"
readonly SERVICE_HOME="/home/hermes-contadoria"
readonly HERMES_HOME="${SERVICE_HOME}/.hermes"
readonly RUNTIME_DIR="/opt/hermes-contadoria-runtime"
readonly ENV_FILE="${HERMES_HOME}/.env"
readonly SERVICE_NAME="hermes-contadoria.service"

ENV_TMP=""

cleanup() {
  if [[ -n "${ENV_TMP}" && -f "${ENV_TMP}" ]]; then
    rm -f -- "${ENV_TMP}"
  fi
}
trap cleanup EXIT

fail() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "execute como root: sudo bash configure.sh"
[[ -t 0 && -t 1 ]] || fail "execute em um terminal interativo para proteger as credenciais"
id "${SERVICE_USER}" >/dev/null 2>&1 || fail "execute install.sh antes"
[[ -x "${RUNTIME_DIR}/venv/bin/python" ]] || fail "runtime Hermes ausente; execute install.sh antes"
[[ -f "${HERMES_HOME}/.no-bundled-skills" ]] || fail "marcador sem-skills ausente"

printf 'Token do bot Telegram (a entrada ficará oculta): '
IFS= read -r -s telegram_token
printf '\n'
[[ "${telegram_token}" =~ ^[0-9]{6,15}:[A-Za-z0-9_-]{20,}$ ]] || \
  fail "formato do token Telegram inválido"

printf 'User ID permitido no Telegram (somente números; vários separados por vírgula): '
IFS= read -r telegram_users
telegram_users="${telegram_users//[[:space:]]/}"
[[ "${telegram_users}" =~ ^[0-9]+(,[0-9]+)*$ ]] || \
  fail "informe IDs numéricos separados por vírgula"

printf 'Chave OpenAI opcional para transcrição (Enter para pular; entrada oculta): '
IFS= read -r -s openai_key
printf '\n'

printf 'Chave ElevenLabs opcional para voz (Enter para pular; entrada oculta): '
IFS= read -r -s elevenlabs_key
printf '\n'

elevenlabs_voice=""
if [[ -n "${elevenlabs_key}" ]]; then
  printf 'Voice ID da ElevenLabs: '
  IFS= read -r elevenlabs_voice
fi

for optional_value in "${openai_key}" "${elevenlabs_key}" "${elevenlabs_voice}"; do
  [[ "${optional_value}" != *$'\r'* && "${optional_value}" != *$'\n'* ]] || \
    fail "uma credencial opcional contém quebra de linha inválida"
done

ENV_TMP="$(mktemp "${HERMES_HOME}/.env.tmp.XXXXXX")"
{
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "${telegram_token}"
  printf 'TELEGRAM_ALLOWED_USERS=%s\n' "${telegram_users}"
  printf 'OPENAI_API_KEY=%s\n' "${openai_key}"
  printf 'ELEVENLABS_API_KEY=%s\n' "${elevenlabs_key}"
  printf 'ELEVENLABS_VOICE_ID=%s\n' "${elevenlabs_voice}"
} > "${ENV_TMP}"

install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
  "${ENV_TMP}" "${ENV_FILE}"
rm -f -- "${ENV_TMP}"
ENV_TMP=""

unset telegram_token openai_key elevenlabs_key

printf '\n>> Abrindo o assistente oficial do Hermes para escolher e autenticar o provedor de IA...\n'
(
  cd "${HERMES_HOME}"
  sudo -u "${SERVICE_USER}" -H env \
    HOME="${SERVICE_HOME}" \
    USER="${SERVICE_USER}" \
    LOGNAME="${SERVICE_USER}" \
    HERMES_HOME="${HERMES_HOME}" \
    VIRTUAL_ENV="${RUNTIME_DIR}/venv" \
    PATH="${RUNTIME_DIR}/venv/bin:${RUNTIME_DIR}/node_modules/.bin:/usr/local/bin:/usr/bin:/bin" \
    "${RUNTIME_DIR}/venv/bin/python" -m hermes_cli.main setup < /dev/tty
)

printf '\n>> Configurando rotas conforme as assinaturas conectadas...\n'
sudo -u "${SERVICE_USER}" -H env \
  HOME="${SERVICE_HOME}" \
  USER="${SERVICE_USER}" \
  LOGNAME="${SERVICE_USER}" \
  HERMES_HOME="${HERMES_HOME}" \
  VIRTUAL_ENV="${RUNTIME_DIR}/venv" \
  PATH="${RUNTIME_DIR}/venv/bin:${RUNTIME_DIR}/node_modules/.bin:/usr/local/bin:/usr/bin:/bin" \
  "${RUNTIME_DIR}/venv/bin/python" "${HERMES_HOME}/scripts/configure_multi_ai.py"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  printf '\nServiço %s ativo.\n' "${SERVICE_NAME}"
  printf 'Agora envie /start e depois uma mensagem ao bot para validar o Telegram em runtime.\n'
else
  systemctl status "${SERVICE_NAME}" --no-pager || true
  fail "o serviço não ficou ativo; consulte journalctl -u ${SERVICE_NAME}"
fi
