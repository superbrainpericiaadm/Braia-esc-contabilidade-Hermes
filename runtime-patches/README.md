# Runtime Braia: roteamento por tarefa

O patch substitui A/B/C por escolha de modelo por subtarefa na família ativa,
fallback capaz e prioridade aprendida. A base é Hermes 0.21.0, árvore Git
`59d8aa0a3099a0a74aab6f9abd2c4ffcc8ddf5d2`, distribuída a partir de
`29112bef099274229cadff79cdff7bf7b99c4b77`. Outra árvore requer validação própria.

Alvos: `tools/delegate_tool.py`, `hermes_cli/config_defaults.py`, `run_agent.py`,
`agent/conversation_loop.py`, `tools/process_registry.py`,
`tools/async_delegation.py` e novo `agent/braia_routing.py`.
O helper de aplicação/migração é mantido separadamente desta implementação.

```text
python /CAMINHO/DO/KIT/scripts/apply_runtime_patch.py --runtime /CAMINHO/DO/HERMES --backup-root /CAMINHO/PRIVADO/DE/BACKUPS --check
python /CAMINHO/DO/KIT/scripts/apply_runtime_patch.py --runtime /CAMINHO/DO/HERMES --backup-root /CAMINHO/PRIVADO/DE/BACKUPS
```

Executar como proprietário do runtime e conferir o resultado concreto do helper.
A coordenação da atualização controla parada/retorno do serviço e migração do
patch anterior. Este documento não autoriza rollout. Sem braia_routing ou com
enabled false, mantém o Hermes nativo e o fallback explícito anterior.

## Fluxo da Braia

1. Antes da subtarefa planejada, `delegate_task(action="route", message=<JSON>)`
   recebe task_type/complexity/acceptance_id, como única ferramenta da resposta e
   sem filhos ativos. Aplica o provedor nessa fronteira e devolve catálogo ativo;
   não depende de uma segunda mensagem do usuário.
2. Enviar tasks[] com goal, context e routing: model, effort, task_type,
   complexity, justification, acceptance_id. Cada modelo precisa ser autorizado
   nessa família e ter capacidade suficiente para seu trabalho.
3. Verificar a entrega contra o critério. Só então `review` recebe JSON com
   execution_id, verdict accepted/rejected, evidence_path absoluto,
   evidence_sha256, verifier e rework opcional. Confere propriedade do filho e
   perfil/sessão persistida, existência e SHA256 do artefato. Filho não revisa
   a própria entrega. A conclusão em segundo plano inclui o identificador;
   outra instância da mesma sessão pode conferir e registrar o aceite.

Texto gerado, HTTP, schema válido e arquivo existente não são qualidade aprovada.
Hash vincula a evidência; a avaliação semântica continua responsabilidade do
revisor. A API `agent.braia_routing.record_verdict` também permite verificadores
externos/humanos. O resultado traz IDs por tentativa e quality unreviewed.

## Configuração e persistência

braia_routing usa schema_version 1, policy_version, initial_provider,
initial_model, initial_tier, manual_provider, providers, fallback e learning.
Cada modelo do catálogo declara tier routine/normal/complex/exceptional, efforts
e default_effort; cada provedor declara orchestrator_model. A lista contém
candidatos e não comprova entitlement. availability unavailable exclui o modelo;
404 permite contingência. Claude exige assinatura OAuth, não API key.

Preferência manual e `/model` prevalecem. Preferência desconectada continua salva;
fallback exige tier conhecido (initial_tier/manual_tier) e alternativa capaz.
Orquestrador configurado não precisa ter o tier do filho complexo. Modelo sem
suporte a esforço usa effort null e não recebe esforço herdado.

Learning defaults experimentais: min_samples 8, sustained_windows 2, window_days
30, promotion_margin 0.15, cooldown_seconds 86400. Exigem **16 veredictos por
provedor**, em grupos distintos e com o modelo, tier e esforço efetivo
comparáveis. Evidência de outro modelo/tier/esforço é isolada e não promove.
Esses parâmetros não estão homologados. Qualidade/confiabilidade
não caem para comprar latência; retrabalho não aumenta. Compare rotina,
complexidade, critério, versão e catálogo; sem suporte suficiente mantém ordem.
Desligar aprendizado restaura a preferência inicial.

SQLite braia-routing.sqlite3 fica junto ao SessionDB do perfil. Transações
protegem processos concorrentes; veredictos são idempotentes e conflitos são
rejeitados. Histórico contém hashes/IDs, modelos, status classificados, latência,
consumo observado opcional, retrabalho e motivos numéricos; não contém prompts,
justificativas, paths ou credenciais. Limites: janela temporal, 10 mil execuções e
mil promoções. Evidência do orquestrador é separada da dos trabalhadores;
planejamento usa resultados comparáveis dos trabalhadores para escolher provedor.
Continuações após fallback ficam em classe separada: seu aceite é auditável,
mas latência/qualidade parcial não promove um provedor como se tivesse feito a
tarefa inteira. Consumo observado usa deltas dos contadores de tokens do Hermes.

Fallback limita inicial + uma alternativa de capacidade equivalente/superior.
Continua a chamada com histórico; não reexecuta a tarefa inteira. Retries nativos
finitos por provedor permanecem. Cota, auth, timeout, sobrecarga, servidor e modelo
indisponível são disponibilidade. Qualidade/política/formato não acionam troca.
Esforço explícito é mantido na alternativa; candidato incompatível é excluído e,
sem opção restante, a troca falha fechada.

Este patch diverge deliberadamente do artefato genérico da fonte v1.0.11: a
especialização contábil corrige isolamento adaptativo por capacidade/esforço e
preservação de esforço no fallback. O hash publicado deste patch deve, portanto,
ser próprio deste repositório e não byte-idêntico ao da distribuição pericial.

## Limites e validação

Testes exercitam política, persistência, concorrência, schema, construção e
dispatch do Hermes e laço real com respostas de transporte substituídas.
Essas provas **não homologam inferência nem qualidade das assinaturas**. Antes
de publicar, validar modelos reais, promoção/retorno, fallback, Telegram e
preservação de dados com pacote integrado e perfil alvo.

Hermes resolve credenciais pelo HERMES_HOME do processo. Bloqueia troca quando
SessionDB pertence a outro perfil; não muda env global entre threads. Review na
conversa exige o vínculo persistido do filho com a sessão raiz e o perfil do
host. Reconstrução do agente e continuações de compressão mantêm o vínculo;
outra conversa, ramo ou perfil não o herda. IDs anteriores à migração não
recebem autoridade retroativa: verificador confiável pode revisá-los via API
Python. Prioridade aprendida persiste. Não há coleta adicional de amostras nem
duplicação de ações em produção.
