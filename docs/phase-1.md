# Fase 1 — Alves-IA / Alves-Agent

Status: testes locais de segurança e HTTP executados; integração PostgreSQL pendente. Não aprovada
para produção. As fases 2–7 ainda não foram implementadas.

## Decisões

- PostgreSQL é obrigatório; SQLite não valida Row-Level Security.
- A API usa alves_app, sem superuser/BYPASSRLS e com permissão somente de
  leitura nas tabelas de identidade. Migrações/provisionamento usam alves_owner.
- Usuários são identidades globais; clínicas e vínculos têm RLS forçada.
- Contexto clinic_id é local à transação. JWT vincula usuário e clínica;
  cada requisição revalida usuário, clínica e vínculo ativos no banco.
- Tokens HS256 expiram em 15 minutos; não há refresh nesta entrega.
- Não há cadastro público. Provisionamento é operação explícita do operador.
- SQLAlchemy síncrono em rotas síncronas, com uma transação por requisição.

## Executar localmente

No Fedora/SELinux, o script de inicialização é montado com `ro,z`: leitura
somente e rótulo compartilhado entre os containers de desenvolvimento e teste.
Isso permite a leitura do arquivo sem desativar o SELinux. Se o container de
teste já falhou ao ler o script, recrie somente o banco descartável de testes
com `docker compose -p alves-ia-test -f compose.test.yaml up -d --force-recreate db`
antes de repetir a suíte. A recriação descarta os dados temporários desse banco.

1. Instale: `python -m pip install -e ".[dev]"`.
2. Complete as novas variáveis de `.env.example` no `.env` existente, sem
   sobrescrever suas chaves. Gere três segredos distintos com
   `openssl rand -hex 32`. Ajuste as senhas nas URLs; não versione `.env`.
3. Execute `docker compose up --build -d`.
4. Provisione um administrador:

   `docker compose run --rm migrate python -m alves_ia.provision --clinic "Minha clínica" --email "admin@example.com"`

5. Em `/docs`, chame `POST /v1/auth/token` com email, password e clinic_id
   retornado pelo provisionamento. Use o token Bearer para `/v1/me` e `/v1/clinic`.

O script inicial de papel PostgreSQL roda apenas em volume novo. Alterar a senha
no `.env` não altera automaticamente papéis de um banco existente. Não remova
volumes com dados para resolver diferenças de configuração.

## Validação obrigatória antes da Fase 2

Uma suíte isolada está disponível, sem portas de banco expostas, sem uso do
volume de desenvolvimento e sem necessidade de preencher `.env`:

```bash
docker compose -p alves-ia-test -f compose.test.yaml run --build --rm tests
```

Esse comando cria PostgreSQL em armazenamento temporário, executa as migrações
e testa também login HTTP, senha incorreta, acesso a outra clínica e revogação
de vínculo com token ainda válido. Precisa do plugin Docker Compose e acesso
ao daemon Docker. Para encerrar apenas essa infraestrutura descartável:

```bash
docker compose -p alves-ia-test -f compose.test.yaml down
```

Execute `pytest`, `ruff check .` e `mypy src tests`. Para isolamento real,
provisione um banco exclusivo de testes com o papel alves_app, aplique
`MIGRATION_DATABASE_URL=... alembic upgrade head` e configure
TEST_OWNER_DATABASE_URL e TEST_DATABASE_URL para esse banco. Execute
`pytest tests/test_tenancy.py`; resultado skipped não conta como aprovação.

O teste cria duas clínicas e verifica isolamento mesmo em consultas sem filtro,
limpeza do contexto no pool e rejeição de usuário sem vínculo com outra clínica.
Login HTTP, revogação de vínculos e migração em banco vazio estão incluídos na
suíte Docker e precisam passar antes de considerar esta fase concluída.

## Pendências e sequência

- Fase 1: executar testes com dependências instaladas e PostgreSQL real;
  ampliar testes HTTP/RBAC e limitar tentativas de login antes de exposição pública.
- Fase 2: WhatsApp, validação de assinatura, Inbox/Outbox e Celery/Redis mínimos.
- Fase 3: tool calling, memória isolada, áudio e handoff.
- Fase 4: agenda e restrição transacional contra sobreposições.
- Fase 5: lembretes e recuperação de notificações.
- Fase 6: APIs administrativas e indicadores.
- Fase 7: segurança, observabilidade e operação de produção.

Referências de implementação:
- https://www.postgresql.org/docs/17/ddl-rowsecurity.html
- https://docs.sqlalchemy.org/en/20/orm/session_transaction.html
