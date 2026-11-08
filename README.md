# Alves IA

A plataforma se chama **Alves-IA**, e seu agente de atendimento, **Alves-Agent**.

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

