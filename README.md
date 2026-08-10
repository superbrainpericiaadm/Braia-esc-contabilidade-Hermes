# Braia para Escritórios de Contabilidade — Hermes

Kit público da Comunidade ContadorIA para instalar o [Hermes Agent](https://github.com/NousResearch/hermes-agent) em uma VPS Ubuntu e iniciar a Braia com uma equipe virtual voltada a escritórios de contabilidade.

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
- persona autocontida da Braia e arquivos dos nove papéis;
- serviço `systemd` para funcionamento contínuo;
- integração com Telegram configurada com as credenciais do próprio usuário.

## O que não acompanha o kit

- nenhuma skill;
- tokens, chaves, senhas ou autenticações;
- conversas, memórias, sessões, logs, bancos ou caches;
- dados de clientes ou dados da instância usada como referência.

O instalador chama o Hermes oficial com `--no-skills` e mantém o arquivo `.no-bundled-skills`, que impede a reinjeção das skills empacotadas em atualizações futuras.

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

## Comandos úteis

```bash
systemctl status hermes-contadoria.service
journalctl -u hermes-contadoria.service -f
systemctl restart hermes-contadoria.service
```

## Segurança e limites

A equipe é demonstrativa e opera em modo assistido. Transmissões, protocolos, assinaturas, pagamentos e decisões tributárias, trabalhistas ou societárias relevantes exigem validação humana do contador responsável.

Leia [NOTICE.md](NOTICE.md) para os créditos e a licença do runtime instalado.

