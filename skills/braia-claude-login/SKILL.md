---
name: braia-claude-login
description: Conecta a assinatura Claude pela conversa com a Braia.
version: 1.0.11
platforms: [linux]
metadata:
  hermes:
    tags: [braia, claude, anthropic, login, oauth]
    category: braia-contabilidade
---

# Conectar Claude pela conversa

Use quando o Chefe pedir para conectar ou reconectar a assinatura Claude,
inclusive depois de uma atualização. O cliente não abre terminal. A Braia
executa o helper; o cliente apenas autoriza no site Claude e devolve o código.
Faça isso na conversa privada com o dono autorizado desta instalação.

## Iniciar

Execute com `terminal`, no usuário e perfil do gateway, o Python do ambiente
Hermes e o caminho absoluto de `scripts/claude_login.py` desta skill, ação
`start`. Use os caminhos da instalação atual, nunca os de outra VPS.

Envie ao dono somente o link retornado em `AUTH_URL` e explique:
"Abra este link, entre na sua conta Claude e autorize. Depois copie o código
completo mostrado no site e me envie aqui para eu concluir."
O código completo inclui a parte depois de `#`. Não peça senha, API key,
access token ou refresh token. Não peça que o cliente execute comandos.
Repetir `start` reutiliza o link pendente por até 15 minutos.

## Concluir quando o código chegar

1. Inicie o mesmo helper, ação `exchange`, com `terminal` usando
   `pty=true` e `background=true`. O comando contém apenas caminhos fixos e
   a ação; nunca interpole o código recebido em comando shell ou argumento.
2. Aguarde o prompt `AUTH_CODE` usando `process` e envie o código completo
   com `process(action="submit", session_id=..., data=...)`. A leitura oculta
   o eco e termina ao receber a linha. Use o identificador retornado pelo
   terminal. Não repita o código em resposta, memória, relatório ou arquivo.
3. Consulte a saída e o exit code do processo. O helper valida o estado e a
   expiração, troca o código via OAuth e grava a credencial renovável no
   armazenamento nativo do Hermes. Ele executa o configurador de rotas,
   preservando a escolha inicial do orquestrador e as contas existentes.
4. Se aparecer `SUCCESS`, faça uma inferência curta usando o provedor Claude
   conectado e o modelo configurado, sem imprimir credenciais. Depois execute
   o helper com ação `reload`: o gateway recarrega ao terminar esta conversa.
   Informe conexão e configuração confirmadas; só afirme inferência validada se
   o teste responder. Se houver falha, relate a etapa e preserve a conta.

## Preferência e seleção por tarefa

Uma conta basta. A segunda é opcional: conectá-la habilita fallback e
prioridade adaptativa sem trocar imediatamente o orquestrador escolhido.
A preferência inicial fica salva. A Braia escolhe o modelo de cada subagente
dentro do provedor ativo; agentes e skills não fixam modelo. Sonnet/Terra são
o padrão, Haiku/Luna atendem rotinas delimitadas, Opus/Sol tarefas complexas e
Fable/Astra exceções justificadas, sempre conforme catálogo da assinatura.

O configurador preserva Google, memórias, sessões e ajustes alheios às rotas.
Instabilidade temporária permite fallback com tentativas limitadas, sem apagar
contas. Aceites verificados e confiabilidade em tarefas comparáveis podem
promover outro provedor entre tarefas; escolha manual explícita prevalece.
Teste de conexão e HTTP 200 não entram no histórico como entrega aceita.

## Recuperação

- Código incompleto ou vencido: peça o código completo ou gere novo link
  após `cancel`. Essa ação limpa apenas o login pendente, sem desconectar contas.
- Se a credencial foi salva mas a configuração falhou, corrija a causa e use
  `finish`. Não repita a troca de um código já consumido. Não declare sucesso
  apenas porque um token existe.
- Não use `import-existing` como login de um novo cliente; essa ação é de
  manutenção e só pode reutilizar a conta do próprio dono da instalação.
- Não habilite uso extra, crie API key, copie conta de outra VPS ou imponha
  troca de preferência por presunção. Nunca exponha arquivos de autenticação.

## Escopo

Este é o frontend PKCE da Braia, integrado ao OAuth do Hermes. Não o apresente
como binário oficial Claude Code. O fluxo aqui atende instalações atualizadas;
o onboarding de novas instalações pelo Worker será tratado em outra etapa.
Inferência bem-sucedida comprova funcionamento, não extrato de consumo.
