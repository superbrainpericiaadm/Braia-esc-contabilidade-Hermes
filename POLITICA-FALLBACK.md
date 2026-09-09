# Seleção, contingência e aprendizado

Uma assinatura basta. O cliente escolhe o orquestrador; a preferência inicial
fica salva. A segunda assinatura habilita fallback e prioridade adaptativa,
sem trocar o provedor imediatamente e sem apagar ou substituir contas.

## Modelo por tarefa

A Braia escolhe o modelo de cada subagente dentro do provedor ativo, incluindo
lotes com modelos diferentes. Agente e skill definem competência e método.
Sonnet/Terra são o padrão; Haiku/Luna atendem rotinas delimitadas e verificáveis;
Opus/Sol atendem tarefas complexas; Fable/Astra são exceções justificadas, se
disponíveis. Delimitação, ambiguidade, dependências, impacto, verificação,
contexto e ferramentas sustentam a escolha, sem classificação por palavras-chave
nem percentual obrigatório de uso. Dados ausentes exigem contexto adicional.

O catálogo confirma identificadores conhecidos e capacidades revisadas, vinculados
aos provedores com assinatura OAuth conectada. A disponibilidade de um candidato
é `unverified` até confirmação: nomes conhecidos da API ou sintetizados pelo seletor
não comprovam acesso pela conta. Modelos retirados não são novos padrões.
O esforço segue a capacidade do modelo: não enviar automaticamente `low` para Haiku.

## Falha de qualidade e indisponibilidade

Qualidade insuficiente exige critério verificável, correção do briefing ou
modelo mais capaz na mesma família. Cota esgotada, autenticação indisponível,
timeout e falha do provedor permitem outra assinatura conectada, com capacidade
adequada. Não reduzir capacidade de uma tarefa difícil apenas porque houve queda.
Se não houver alternativa capaz, devolver bloqueio explícito.

Novos filhos acompanham o provedor em que o orquestrador está operando, inclusive
durante fallback. A troca temporária não promove por si só o provedor, não apaga
contas e não interrompe uma tarefa em andamento. Tentativas são limitadas; antes
de repetir uma ação com possível efeito externo, conferir o estado do destino.

## Prioridade aprendida

Comparar tarefas da mesma classe, complexidade e critério de aceitação, na mesma
versão de política e modelo. Registrar aceite ou rejeição após validação da
entrega, motivo verificável, provedor/modelo, latência, retrabalho e falhas.
HTTP 200, texto gerado e teste de conexão não são aceite. Erro de conteúdo,
falta de dados, falha externa e indisponibilidade do provedor são causas distintas.
Não gravar credenciais nem conteúdo integral do cliente no histórico.

Qualidade aceita e confiabilidade vêm primeiro; tempo e consumo observado são
critérios posteriores. Promoção exige amostra mínima de cada candidato, dados
recentes, vantagem sustentada e intervalo entre mudanças, em parâmetros
versionados sujeitos à calibração no laboratório. Sem evidência suficiente,
manter a ordem vigente. Persistir ordem e motivo; aplicar mudanças entre tarefas.
A escolha manual explícita prevalece e pode fixar a ordem ou reiniciar o
aprendizado. Desligar o aprendizado permite retornar à preferência inicial.

Usar execuções reais já realizadas ou avaliações controladas. Não duplicar
ações externas nem enviar tarefas adicionais a outra assinatura em produção
apenas para coletar amostras. Mudanças de modelo/política separam as amostras.

## Configuração e autenticação

`scripts/configure_multi_ai.py` consulta os candidatos do Hermes e complementa
catálogos antigos com a matriz revisada da distribuição, marcada como
`source: reviewed_policy` e `availability: unverified`. Isso permite concluir o
login por conversa mesmo quando o cache omite modelos conhecidos, sem presumir
acesso pela conta. Conserva a escolha em `model` e
`braia_routing.initial_provider`. `--check` valida sem escrever configuração;
`--catalog` aceita JSON revisado no formato descrito abaixo. Ao gravar, o helper
preserva backup integral, escreve atomicamente e não repete a escrita sem mudanças.
Não altera o histórico persistido de aprendizado.

O template usa `initialized: false`. Na primeira configuração, o helper captura
a preferência do `model` existente do cliente e grava `initialized: true`.
Assim, o merge de chaves novas feito pelo atualizador não impõe a preferência
de fábrica a um cliente que já escolheu Claude. Não redefinir esse marcador
em um perfil inicializado.

```json
{"anthropic":{"models":{"claude-sonnet-5":{"tier":"normal","efforts":["medium","high"],"default_effort":"medium","availability":"unverified"}}}}
```

Incluir todos os provedores conectados no catálogo fornecido, um modelo normal
por provedor e o modelo de orquestração escolhido. Os tiers são `routine`,
`normal`, `complex` e `exceptional`; `efforts: []` e `default_effort: null`
omitem esforço. Disponibilidade admite `unverified`, `available` e `unavailable`;
o último exclui o candidato. Somente marcar `available` com evidência da conta.
O arquivo não contém credenciais. Um modelo desconhecido ou sem capacidade
revisada exige revisão do catálogo; não há substituição silenciosa do escolhido.

`braia_routing.manual_provider` fixa uma preferência explícita. Definir como
`null` libera a prioridade adaptativa; `learning.enabled: false` retorna à
preferência inicial, salvo ordem manual vigente. Os parâmetros iniciais são
experimentais: 8 amostras por janela e duas janelas sustentadas (16 tarefas
completas e revisadas por candidato), retenção de 30 dias, margem 0,15 e
intervalo de 86400 segundos entre mudanças. Esses valores ainda precisam de
calibração com a carga representativa da nossa infraestrutura.

Claude usa OAuth de assinatura, sem API key nem ativação de uso extra. O login
privado por conversa continua na skill `braia-claude-login`, com PKCE integrado
ao Hermes. Inferência comprova funcionamento, não extrato de consumo ou aceite
de entrega. Não copiar contas de outras instalações.

O runtime precisa das extensões de `runtime-patches/README.md`; o configurador
recusa uma interface incompatível. A arquitetura da v1.0.11 foi validada no
laboratório, incluindo delegação real, conclusão assíncrona e revisão
persistida. Os limites, fluxo e critérios operacionais estão descritos de forma
autocontida neste documento e em `runtime-patches/README.md`. A atualização exige
validação em cada instalação; a publicação não inicia o lote de clientes. O Worker de novas
instalações pertence a uma etapa separada.
Não empacotar dados, endereços, backups, histórico ou credenciais de instalação.
