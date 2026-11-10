# Fase 3 — Atendimento com IA e áudio

Status: implementação local concluída; requer execução da suíte PostgreSQL e
homologação com uma conta/número de teste antes de usar dados reais.

## Fluxo de texto e voz

- Mensagens de texto são guardadas no histórico da clínica e geram resposta
  automática somente quando `AI_LIVE_ENABLED=true` e a conversa está em
  `BOT_ACTIVE`.
- Eventos de áudio entram pela mesma Inbox durável. Quando a IA está habilitada,
  o worker agenda `transcribe_audio`; o webhook não baixa mídia nem chama LLM.
- O worker obtém o arquivo via Graph API e aceita a URL temporária somente em
  `https://lookaside.fbsbx.com/whatsapp_business/attachments/...`. Redirecionamentos
  são bloqueados para evitar encaminhar o token para outro host.
- O download é limitado a 16 MiB, exige MIME suportado e confere tamanho e
  SHA-256. O arquivo permanece em memória e é descartado após a transcrição.
- A transcrição usa OpenAI Whisper (`whisper-1`) com `verbose_json`. A confiança
  é a média de `avg_logprob` dos segmentos; texto vazio, ausência de segmentos
  ou média abaixo de -1,0 resulta em handoff auditado como solicitação ambígua.
- Transcrição válida passa a ser uma mensagem de usuário e entra no fluxo já
  existente do agente. Falha no download/Whisper gera handoff auditado e código
  de erro sanitizado. A conversa é revalidada sob lock antes de qualquer resposta.
- A mídia original e a URL temporária não são persistidas. A Inbox mantém o
  payload original do webhook, incluindo o ID da mídia e metadados enviados pela
  Meta; mensagens/transcrições ficam no histórico segregado por clínica/RLS.

## Configuração

`AI_LIVE_ENABLED=false` é o padrão. Para homologar, configure no worker:

```env
AI_LIVE_ENABLED=true
AI_PROVIDER=gemini
GEMINI_API_KEY=...
OPENAI_API_KEY=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_API_VERSION=vNN.N
```

`OPENAI_API_KEY` é necessária para transcrição mesmo quando `AI_PROVIDER=gemini`.
O serviço API não recebe as chaves de IA; somente o worker as recebe. Atualize a
migração 0005 antes de iniciar API/worker. A ativação envia mensagens e áudio de
clientes ao provedor externo escolhido; use somente números e dados de teste na
homologação.

## Validação

```bash
pytest
ruff check .
mypy src
docker compose -p alves-ia-test -f compose.test.yaml run --build --rm tests
```

Os testes unitários simulam Graph API e Whisper. A suíte Docker exercita o ciclo
Inbox → transcrição → agente → Outbox e a transferência por baixa confiança,
sem chamadas reais a Meta, Gemini ou OpenAI. Homologação real de mídia continua
necessária para confirmar permissões, número, versão do Graph API e formatos
aceitos pela conta Meta.

## Limites

- Formatos aceitos pelo adaptador: OGG, MPEG, MP4, MPGA, WAV, WebM, FLAC e M4A.
  Mídias fora da lista são transferidas para atendimento humano.
- Confiança de Whisper é uma heurística técnica, não uma medida clínica nem
  garantia de transcrição correta. A confirmação humana é necessária para dados
  críticos.
- Ainda não há controles de retenção/expurgo para Inbox, mensagens e transcrições.
- Áudio não é armazenado, portanto a interface humana futura precisará fornecer
  acesso controlado à mídia via Meta se o atendente precisar ouvi-la.

## Consulta ao Google Sheets

O worker pode pesquisar uma planilha Google explicitamente conectada e
configurada para a clínica. A integração usa escopo OAuth somente leitura,
armazena o token de atualização criptografado e restringe as consultas à faixa
finita e às colunas liberadas pelo gestor. O agente recebe no máximo cinco
linhas correspondentes como referência não confiável. Falhas de acesso ou
configuração provocam handoff humano; o modelo não recebe credenciais, IDs ou
faixas arbitrárias para executar.

Siga o [guia do conector Google Sheets](google-sheets.md) e aplique a migração
0006. Excel/CSV ainda não está implementado.
