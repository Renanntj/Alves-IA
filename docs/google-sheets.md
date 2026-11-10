# Conector Google Sheets

O conector permite ao Alves-Agent consultar dados de referência mantidos pela
clínica em uma planilha. Nesta etapa ele é somente leitura; não altera células,
não marca compromissos e não substitui o PostgreSQL para agenda ou reservas.
Importação de Excel/CSV fica para uma etapa posterior.

## Preparar o Google Cloud

1. Crie ou selecione um projeto no Google Cloud e habilite a Google Sheets API.
2. Configure a tela de consentimento OAuth e cadastre os usuários de teste
   enquanto o aplicativo estiver em modo de teste.
3. Crie um cliente OAuth do tipo aplicação Web.
4. Cadastre como URI de redirecionamento autorizada o valor exato de
   GOOGLE_OAUTH_REDIRECT_URI, por exemplo
   https://api.seudominio.com/v1/integrations/google-sheets/callback.
5. Não solicite escopos além de
   https://www.googleapis.com/auth/spreadsheets.readonly.

O escopo spreadsheets.readonly é classificado pelo Google como sensível. Para
uso público, o processo de publicação pode exigir verificação do consentimento
e justificativa do escopo. Consulte a [documentação de escopos da Sheets
API](https://developers.google.com/workspace/sheets/api/scopes) e o [fluxo
OAuth para aplicações web](https://developers.google.com/identity/protocols/oauth2/web-server).

## Configurar o serviço

Preencha no .env:

```env
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=https://api.seudominio.com/v1/integrations/google-sheets/callback
GOOGLE_SHEETS_ENCRYPTION_KEY=...
```

Gere uma chave independente do JWT_SECRET:

```bash
openssl rand -base64 32
```

Guarde-a em um gerenciador de segredos e faça backup protegido. Perder ou
substituir essa chave impede a descriptografia dos tokens já armazenados e
exige que as clínicas conectem novamente as contas Google. A chave deve estar
disponível para API e worker. Não a envie ao navegador, não a registre em logs e
não a versione.

Aplique a migração 0006:

```bash
docker compose run --rm migrate
```

## Conectar e limitar a consulta

Use um token de usuário com papel admin ou manager na clínica. O fluxo:

1. POST /v1/integrations/google-sheets/connect retorna authorization_url.
   Abra a URL e autorize a conta que tem acesso à planilha.
2. O Google envia o callback form_post para a URL cadastrada. A aplicação
   valida o estado assinado de uso único, a clínica e a associação ativa do
   usuário.
3. Consulte GET /v1/integrations/google-sheets/status.
4. Configure a planilha:

```http
PUT /v1/integrations/google-sheets/configuration
Authorization: Bearer <token>
Content-Type: application/json

{
  "spreadsheet_id": "ID_COPIADO_DA_URL_DA_PLANILHA",
  "range_name": "'Serviços'!A1:Z100",
  "allowed_columns": ["Nome", "Descrição", "Preço"]
}
```

O gestor deve selecionar apenas dados necessários ao atendimento. A faixa tem
de ser limitada, iniciar em A1 e ficar dentro de 501 linhas por 26 colunas.
São permitidas até 12 colunas explicitamente liberadas. O backend valida se
elas existem como cabeçalho antes de salvar a configuração.

DELETE /v1/integrations/google-sheets/connection remove a conexão e tenta
revogar o token no Google. GET /status, conexão, configuração e remoção
exigem papel admin ou manager.

## Dados e limites

- O backend armazena somente o refresh token criptografado com AES-256-GCM;
  tokens de acesso são temporários e não são persistidos.
- Estado OAuth e conexão ficam sob Row-Level Security, vinculados ao
  clinic_id. O callback confirma novamente a associação e seu papel.
- Cada busca lê somente a faixa configurada no Google, filtra localmente e
  retorna até cinco linhas contendo apenas colunas liberadas, truncadas a 300
  caracteres por célula.
- A IA só pode solicitar search_clinic_sheet com uma consulta de texto
  limitada. IDs de planilha, intervalos, SQL e funções internas não são
  argumentos disponíveis ao modelo.
- Os valores são conteúdo não confiável. O modelo recebe instrução explícita
  para ignorar comandos que possam estar escritos nas células. Isso reduz o
  risco de prompt injection, mas não torna conteúdo malicioso seguro por si só.
- Quando o agente usa os resultados para responder, as linhas correspondentes
  são enviadas ao provedor escolhido em AI_PROVIDER. Não inclua dados pessoais
  ou sensíveis sem revisar necessidade, base legal, retenção e termos do
  provedor. Comece com uma planilha e contatos de teste.
- Escopo, faixa, campos e limites são defesa em profundidade. Mesmo assim, uma
  resposta generativa pode interpretar incorretamente dados; informações
  críticas devem ser confirmadas por uma pessoa.

A consulta via IA ocorre apenas no worker e continua desligada até
AI_LIVE_ENABLED=true. Para confirmar o OAuth, configure credenciais, aplique
a migração e teste com uma conta e planilha descartáveis antes da homologação.
