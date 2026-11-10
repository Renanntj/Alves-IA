# Alves IA

A plataforma se chama **Alves-IA**, e seu agente de atendimento, **Alves-Agent**.

## Experimentar a IA sem WhatsApp

No `.env`, configure `APP_ENV=development`, `AI_PROVIDER=gemini` e sua
`GEMINI_API_KEY` (ou `AI_PROVIDER=openai` e `OPENAI_API_KEY`). Então execute:

```bash
python -m uvicorn alves_ia.demo:app --host 127.0.0.1 --port 8001
```

Abra **http://127.0.0.1:8001**. Não precisa de Docker, banco ou WhatsApp.
O histórico fica na memória da página e é apagado ao recarregar. As mensagens
são enviadas ao provedor configurado e usam a cota da sua API. Não use dados
reais de pacientes. Esta demonstração não possui ferramentas nem realiza
agendamentos; ela usa a integração textual existente, anterior à Fase 3.
Não publique essa aplicação por proxy ou na internet. O acesso remoto e o
ambiente de produção são bloqueados. Reinicie o processo ao alterar o `.env`.

A fundação de autenticação/PostgreSQL da Fase 1 está em implementação.
Consulte [configuração, validação e pendências](docs/phase-1.md) antes de executar.
A [Fase 2 — WhatsApp](docs/phase-2.md) inclui webhook assinado, Inbox/Outbox,
processamento Celery e envio de texto com controle da janela de atendimento.

### Atendimento com IA (Fase 3)

O worker inicia com `AI_LIVE_ENABLED=false`. Para enviar mensagens recebidas ao
provedor escolhido, habilite explicitamente essa variável e configure somente a
chave do provedor em uso (`AI_PROVIDER=gemini` ou `AI_PROVIDER=openai`). O serviço
da API não recebe as chaves dos provedores; elas ficam restritas ao worker.
Mensagens e histórico são armazenados no PostgreSQL com isolamento por clínica.
Considere os termos de retenção e tratamento de dados do provedor antes de
habilitar em produção. Chamadas de ferramenta são limitadas ao pedido de
atendimento humano; o worker valida os argumentos e executa a transição no
backend. Não há execução de SQL ou ferramentas arbitrárias pelo modelo.

Se o editor não resolver `from google import genai`, selecione o interpretador
`.venv/bin/python` e instale as dependências nesse ambiente:

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -c "from google import genai; print('Gemini SDK disponível')"
```

O pacote que fornece esse import é `google-genai`.

Base de projeto em Python com FastAPI para construir integrações de IA e
automações.

## Requisitos

- Python 3.12 ou superior
- `pip`

## Configuração local

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

No Windows PowerShell, ative o ambiente com:

```powershell
.venv\Scripts\Activate.ps1
```

Edite `.env` e preencha `GEMINI_API_KEY` para usar o provedor padrão. O projeto
também é compatível com OpenAI: defina `AI_PROVIDER=openai` e preencha
`OPENAI_API_KEY`. Esse arquivo não é versionado.

Os modelos podem ser trocados por configuração:

```env
AI_PROVIDER=gemini
GEMINI_MODEL=gemini-2.5-flash
# ou
AI_PROVIDER=openai
OPENAI_MODEL=gpt-4o-mini
```

## Executar a API

```bash
uvicorn alves_ia.main:app --reload
```

A API fica disponível em `http://localhost:8000`. A documentação interativa
está em `/docs` e o health check em `/health`.

## Testes e qualidade

```bash
pytest
ruff check .
mypy src
```

## Estrutura

```text
src/alves_ia/   Código da aplicação
tests/          Testes automatizados
.env.example    Variáveis de ambiente documentadas
pyproject.toml  Dependências e configuração das ferramentas
```

As dependências já incluem FastAPI/Uvicorn, clientes Gemini e OpenAI,
agendamento com APScheduler, retries com Tenacity e logging estruturado com
Structlog para as próximas automações. A integração de IA está em
`src/alves_ia/ai.py`, com uma interface única para os dois provedores.

