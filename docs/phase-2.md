# Fase 2 — WhatsApp

Implementação para homologação. Não ativar em produção antes de executar os
testes PostgreSQL, verificar o processamento Celery e homologar com um número
de teste Meta. Nenhuma chamada real à Meta foi feita pelo agente.

## Escopo

- GET /webhooks/whatsapp: verificação de assinatura de cadastro (verify token).
- POST /webhooks/whatsapp: HMAC-SHA256 do corpo exato, limite de 1 MiB,
  roteamento por WABA + phone_number_id, Inbox e Outbox na mesma transação.
- O webhook confirma somente após commit; Redis não participa do recebimento.
- Deduplicação por clínica, número e identificador de mensagem/evento de status.
- Worker cria/atualiza conversas sem reduzir o timestamp de última interação.
- GET /v1/whatsapp/conversations: últimas 100 conversas da clínica autenticada.
- POST /v1/whatsapp/messages: texto para conversation_id, com Idempotency-Key.
- GET /v1/whatsapp/messages/{id}: estado do envio, entrega e erro sanitizado.
- Celery Beat publica tarefas pendentes a cada 10s; jobs não concluídos podem
  ser publicados novamente após 60s. Efeitos de ingestão são transacionais.
- Respostas livres só são aceitas dentro das 24h e a condição é revalidada
  imediatamente antes da tentativa no worker.
- HTTP 429: atraso exponencial, máximo de cinco tentativas. Timeout/5xx:
  resultado unknown; sem reenvio automático. Tentativas interrompidas também
  são classificadas como unknown após dois minutos.
- Status de entrega não regride de read para delivered/sent. Eventos que
  chegam antes do registro do ID de saída aguardam até cinco novos ciclos.

O registro global channel_routes contém apenas IDs de roteamento e situação,
sem conteúdo ou credenciais. Todas as tabelas de conteúdo têm RLS forçada e
chaves compostas clinic_id/phone_id. A API não pode editar vínculos de números.

## Configurar

1. Atualize dependências: `python -m pip install -e ".[dev]"`.
2. Acrescente ao `.env` as variáveis WHATSAPP_* de `.env.example`. O app secret
   valida o webhook; verify token é um segredo escolhido para o cadastro; o
   access token autoriza envios. Não confunda os três. A versão Graph API deve
   corresponder à versão ativa do seu app Meta.
3. Execute `docker compose up --build -d`. Migração 0002 deve terminar antes
   da API/worker. Redis não tem porta exposta no host. Execute apenas um Beat.
4. Cadastre um número verificado, sem transferir números já vinculados:

```bash
docker compose run --rm migrate python -m alves_ia.whatsapp.provision --clinic-id UUID_DA_CLINICA --phone-id ID_DO_NUMERO --waba-id ID_DA_CONTA
```

5. Publique o callback HTTPS `/webhooks/whatsapp` e assine o campo messages no
   painel Meta. O Compose permanece em localhost; proxy/TLS externo é requisito
   de implantação, não é criado automaticamente.
6. Observe `docker compose logs worker beat`. Uma mensagem enviada ao número
   de teste deve resultar em conversa consultável pela API autenticada.

## Verificação

O Dockerfile configura timeout de socket do pip de 120 segundos e cinco
tentativas de conexão, além de cache de pacotes do BuildKit. Se um download
falhar por timeout, repita o build normalmente, sem `--no-cache`. Essas opções
não garantem retomada de downloads interrompidos nem corrigem indisponibilidade
do PyPI. O cache fica no builder e não é incorporado à imagem final.

```bash
pytest
ruff check .
mypy src tests
docker compose -p alves-ia-test -f compose.test.yaml run --build --rm tests
```

Os testes de banco verificam Inbox deduplicada, Outbox, isolamento de conteúdo,
idempotência de envio e reprocessamento do mesmo job sem nova chamada externa.
O Compose de testes também inicia Redis e um worker real; TEST_QUEUE=1 faz a
suíte publicar um evento via dispatcher e aguardar sua ingestão pelo worker.
Os testes HTTP externos usam MockTransport; não consomem créditos Meta.

## Limites explícitos

- O MVP usa um app Meta e um token de operador autorizado sobre os números
  cadastrados. Onboarding OAuth por clínica e cofre multi-app são evolução futura.
- Templates fora da janela não estão implementados: a operação é recusada.
- Áudio é preservado como evento recebido; download/transcrição pertencem à Fase 3.
- Não há respostas automáticas de IA nesta fase.
- unknown exige análise do operador antes de decidir qualquer reenvio.
  Status sem correspondência ficam preservados na Inbox para reconciliação.
- Rate limiting de entrada, política de retenção, painel de falhas e observabilidade
  de produção ainda precisam de implementação/validação antes de exposição pública.

Referências:
- https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/
- https://docs.celeryq.dev/en/stable/userguide/tasks.html
