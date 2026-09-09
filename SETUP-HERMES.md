# Instalação assistida — Braia ContadorIA no Hermes

Este manual deve ser seguido pelo agente que estiver ajudando o usuário. Leia tudo antes de executar.

## Resultado esperado

- Hermes oficial instalado em uma VPS Ubuntu;
- somente a skill operacional `braia-claude-login`, distribuída pelo kit;
- Braia e os nove papéis contábeis disponíveis por roteamento de persona;
- Telegram restrito aos IDs informados pelo usuário;
- serviço `hermes-contadoria.service` ativo após autenticação e configuração.

## Regras de segurança

1. Peça uma informação por vez.
2. Nunca peça a senha da VPS na conversa. O usuário deve digitá-la diretamente no prompt interativo do SSH, sem compartilhá-la com o agente.
3. Nunca repita, imprima ou grave tokens em histórico, comando, relatório ou arquivo temporário.
4. Use SSH interativo. Não inclua senha em argumento de processo e não use `StrictHostKeyChecking=no`.
5. No primeiro acesso, mostre a fingerprint recebida e peça ao usuário que a confirme com o provedor.
6. Execute somente os scripts deste repositório público. Não reutilize arquivos de outros kits.
7. Não copie skills, memórias, sessões, logs, bancos, caches ou credenciais de outra instalação.
8. Não declare funcionamento do Telegram antes de o usuário enviar uma mensagem e receber resposta.

## Etapa 1 — coletar acesso à VPS

Pergunte separadamente:

1. IP público da VPS;
2. usuário SSH, normalmente `root`.

Não peça a senha. Se a VPS ainda usar autenticação por senha, abra ou peça que o usuário abra o comando abaixo e oriente-o a digitar a senha diretamente quando o terminal solicitar. Se o ambiente do agente não permitir que o usuário controle o prompt interativo, o próprio usuário deve executar esse comando.

Abra uma sessão SSH interativa:

```bash
ssh USUARIO@IP_DA_VPS
```

Confirme Ubuntu 22.04 ou mais recente:

```bash
cat /etc/os-release
uname -m
```

## Etapa 2 — baixar o kit público

Na VPS:

```bash
apt-get update
apt-get install -y git ca-certificates curl
git clone https://github.com/superbrainpericiaadm/Braia-esc-contabilidade-Hermes.git
cd Braia-esc-contabilidade-Hermes
```

Se o diretório já existir, não o apague. Inspecione o estado e retome a etapa que ficou pendente.

## Etapa 3 — instalar o runtime e os agentes

```bash
sudo bash install.sh
```

Esse script:

- instala o Hermes oficial fixado por commit;
- usa obrigatoriamente `--no-skills`;
- cria o usuário de serviço `hermes-contadoria`;
- aplica somente os templates públicos deste repositório e a skill operacional `braia-claude-login`;
- instala a unidade `systemd`, mas ainda não a inicia.

Cheque a instalação:

```bash
test -f /home/hermes-contadoria/.hermes/.no-bundled-skills
find /home/hermes-contadoria/.hermes/skills -mindepth 1 -maxdepth 1 -printf '%f\n'
```

O segundo comando deve produzir somente `braia-claude-login`. Ela não torna Claude obrigatório: a assinatura é opcional e pode ser conectada posteriormente, quando o usuário pedir na conversa privada. Não peça que o cliente use o terminal para conectar Claude.

## Etapa 4 — configurar credenciais e autenticação

Antes de executar, peça ao usuário, um item por vez:

1. token do bot criado no `@BotFather`;
2. user_id numérico obtido no `@userinfobot`;
3. opcionalmente, chave OpenAI para transcrição;
4. opcionalmente, chave ElevenLabs e voice ID para voz.

Em seguida:

```bash
sudo bash configure.sh
```

O script lê o token sem exibi-lo, grava `/home/hermes-contadoria/.hermes/.env` com permissão `0600` e abre o assistente oficial `hermes setup` como o usuário isolado. Ajude o usuário a escolher e autenticar o provedor de IA. Quando houver URL de OAuth, o próprio usuário deve abri-la e autorizar.

## Etapa 5 — validar somente o necessário

```bash
systemctl is-active hermes-contadoria.service
systemctl status hermes-contadoria.service --no-pager
journalctl -u hermes-contadoria.service -n 30 --no-pager
```

Não publique o conteúdo do `.env`, `auth.json` ou logs que possam conter dados do usuário.

Peça ao usuário para abrir o bot, enviar `/start` e depois `oi`. Somente após a resposta real, informe que o Telegram está funcionando.

## Retomada após falha

- Se `install.sh` falhar, corrija a causa e repita somente `install.sh`.
- Se `configure.sh` falhar, preserve a instalação e repita somente `configure.sh`.
- Se o serviço falhar, inspecione `journalctl`, corrija a configuração indicada e reinicie somente o serviço.
- Nunca apague `/home/hermes-contadoria` para tentar novamente.
