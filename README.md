# Braia para Escritórios de Contabilidade — Hermes

Kit público da Comunidade ContadorIA para instalar o [Hermes Agent](https://github.com/NousResearch/hermes-agent) 0.21.0 em uma VPS Ubuntu e iniciar a Braia com uma equipe virtual voltada a escritórios de contabilidade. A v1.0.11 adapta a arquitetura recente de roteamento da família Braia sem importar conteúdo pericial.

O kit instala uma única instância do Hermes. Braia coordena nove papéis por roteamento de persona:

- Braia — coordenação e demandas multidisciplinares;
- Victor — contábil e controladoria;
- Daiane — fiscal e tributário;
- Agnaldo — departamento pessoal e trabalhista;
- Silvana — legalização e societário;
- Paulo — tecnologia e automação;
- Isaura — secretaria e atendimento;
- Angélica — pessoas e contratação;
- Juliana — operações e processos.

## O que este repositório instala

- Hermes Agent em runtime isolado;
- perfil e configuração-base da Comunidade ContadorIA;
- persona autocontida da Braia e arquivos dos nove papéis contábeis;
- roteamento por tarefa dentro da família de assinatura ativa;
- fallback capaz entre assinaturas conectadas e prioridade adaptativa por entregas verificadas;
- login Claude opcional pela conversa, sem terminal do cliente;
- serviço `systemd` para funcionamento contínuo;
- integração com Telegram configurada com as credenciais do próprio usuário.

## O que não acompanha o kit

- nenhuma skill de domínio ou dado de outra instalação; somente a skill operacional de login Claude;
- tokens, chaves, senhas ou autenticações;
- conversas, memórias, sessões, logs, bancos ou caches;
- dados de clientes ou dados da instância usada como referência.

O instalador chama o Hermes oficial com `--no-skills`, mantém `.no-bundled-skills` para impedir a reinjeção do catálogo público e instala apenas a skill operacional versionada neste kit. A extensão de runtime é aplicada com backup e validação filewise sobre a base Hermes 0.21.0 revisada.

## Instalação assistida

Para usuários da Comunidade ContadorIA, o caminho recomendado é abrir o Claude Code no computador e colar o conteúdo de [prompt-instalador.txt](prompt-instalador.txt). O agente seguirá [SETUP-HERMES.md](SETUP-HERMES.md), pedindo uma informação por vez.

## Instalação direta na VPS

Requisitos: VPS Ubuntu 22.04 ou mais recente, acesso `root`/`sudo` e um bot criado no `@BotFather`.

```bash
git clone https://github.com/superbrainpericiaadm/Braia-esc-contabilidade-Hermes.git
cd Braia-esc-contabilidade-Hermes
sudo bash install.sh
sudo bash configure.sh
```

O segundo comando solicita o token do Telegram sem exibi-lo, pede os usuários permitidos e abre o assistente oficial de autenticação do Hermes. Nenhuma credencial é gravada neste repositório.

Uma assinatura ChatGPT ou Claude basta. A segunda é opcional. Se o usuário quiser conectar Claude depois, deve pedir à própria Braia em conversa privada; ela conduz o OAuth sem pedir API key nem abrir terminal do cliente.

## Como testar o kit

No clone do repositório:

```bash
python3 -m pytest -q tests
bash scripts/check-no-secrets.sh
```

Para validar a extensão contra um checkout limpo do Hermes 0.21.0 sem alterar uma instalação:

```bash
python3 scripts/apply_runtime_patch.py --runtime /caminho/hermes-0.21.0 --backup-root /caminho/backups --check
```

Os testes determinísticos validam política, persistência, fallback, concorrência e migração. Eles não comprovam entitlement ou qualidade de uma assinatura real.

## Comandos úteis

```bash
systemctl status hermes-contadoria.service
journalctl -u hermes-contadoria.service -f
systemctl restart hermes-contadoria.service
```

## Segurança e limites

A equipe é demonstrativa e opera em modo assistido. Transmissões, protocolos, assinaturas, pagamentos e decisões tributárias, trabalhistas ou societárias relevantes exigem validação humana do contador responsável.

Leia [NOTICE.md](NOTICE.md) para os créditos e a licença do runtime instalado.

