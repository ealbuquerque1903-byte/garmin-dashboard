# Garmin Report

Baixa atividades do Garmin Connect e envia relatórios em PDF por e-mail.

**Fluxo completo:**

```
Atalho iOS → workflow_dispatch (GitHub Actions)
    → sync.py       (baixa dados do Garmin, atualiza history.json)
    → report.py     (gera PDFs com gráficos e tabelas)
    → send_email.py (envia PDFs por SMTP)
```

Não há site, servidor ou publicação pública. O repositório deve permanecer **privado** — contém histórico de dados de saúde.

---

## Autenticação: fluxo único, autossustentável

```
[Uma vez] python3 login.py (no Mac)
    → .garmin_tokens_v3/garmin_tokens.json
    → copiar JSON para Secret GARMIN_TOKENS

[A cada run] workflow restaura tokens → sync.py usa/renova → workflow
             persiste token atualizado de volta ao Secret GARMIN_TOKENS
```

O Garmin rotaciona o refresh token a cada uso. O step "Persistir tokens renovados" no workflow compara hashes SHA-256 do token antes/depois e, se houver mudança, atualiza o Secret via `gh secret set` usando o `GH_PAT_SECRETS`. Isso elimina a necessidade de intervenção manual enquanto o refresh token estiver válido (duração indefinida enquanto usado regularmente).

**Se chegar e-mail de "FALHA DE AUTENTICAÇÃO":** rode `python3 login.py` no Mac e atualize o Secret `GARMIN_TOKENS` conforme as instruções exibidas.

---

## Configuração inicial passo a passo

### 1. Instalar dependências e gerar tokens

```bash
git clone <este-repo>  # repositório privado
cd garmin-ai
pip install -r requirements.txt
python3 login.py
```

`login.py` pedirá e-mail e senha do Garmin Connect (não são salvos), suporta MFA, e ao final exibe o JSON dos tokens para copiar.

### 2. Configurar Secrets no GitHub

Em **Settings → Secrets and variables → Actions**, criar:

| Secret | Descrição |
|--------|-----------|
| `GARMIN_TOKENS` | JSON exibido ao final do `login.py` |
| `GH_PAT_SECRETS` | Fine-grained PAT com permissão **Secrets: Read and write** neste repositório (necessário para auto-renovação) |
| `SMTP_HOST` | Servidor SMTP (ex.: `smtp.gmail.com`) |
| `SMTP_PORT` | Porta SMTP (`587` para TLS, `465` para SSL) |
| `SMTP_USER` | Usuário SMTP (geralmente o e-mail) |
| `SMTP_PASS` | Senha do SMTP ou App Password |
| `MAIL_FROM` | Endereço remetente |
| `MAIL_TO` | Endereço destinatário |

> Se existirem os Secrets antigos `GARMIN_OAUTH1` e `GARMIN_OAUTH2`, podem ser removidos — foram substituídos por `GARMIN_TOKENS`.

### 3. Criar o PAT GH_PAT_SECRETS

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Clique em **Generate new token**
3. Configuração:
   - **Resource owner:** sua conta
   - **Repository access:** apenas este repositório
   - **Permissions → Secrets:** `Read and write`
   - Todos os outros: `No access`
4. Copie o token gerado para o Secret `GH_PAT_SECRETS`

---

## Atalho do iOS

### Criar o PAT para disparar o workflow

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Clique em **Generate new token**
3. Configuração:
   - **Resource owner:** sua conta
   - **Repository access:** apenas este repositório
   - **Permissions → Actions:** `Read and write`
   - Todos os outros: `No access`
4. Anote a data de expiração

### Criar o Atalho

1. Abra o app **Atalhos** no iPhone
2. Toque em **+** para criar novo atalho
3. *(Opcional)* Adicione a ação **"Escolher no Menu"** com as opções:
   - `atividades`
   - `consolidado`
   - `ambos`
4. Adicione a ação **"Obter Conteúdo de URL"** com:
   - **URL:** `https://api.github.com/repos/{SEU_USUARIO}/{SEU_REPO}/actions/workflows/sync.yml/dispatches`
   - **Método:** POST
   - **Cabeçalhos:**
     - `Authorization` → `Bearer SEU_PAT_AQUI`
     - `Accept` → `application/vnd.github+json`
   - **Corpo:** JSON
     ```json
     {"ref":"main","inputs":{"report_type":"atividades"}}
     ```
     *(Se usou o menu, substitua `"atividades"` pela variável de resultado do menu)*
5. Renomeie para **"Garmin Report"** e adicione à tela de início

Uma resposta HTTP 204 confirma que o workflow foi disparado. O e-mail chega em alguns minutos.

---

## Tipos de relatório

| Opção | Descrição |
|-------|-----------|
| `atividades` | 1 PDF por atividade nova desde o último sync |
| `consolidado` | 1 PDF com tendências e tabelas dos últimos 30 dias |
| `ambos` | Os dois acima |

---

## Estrutura do projeto

```
garmin-ai/
├── sync.py               # Baixa dados do Garmin Connect (garminconnect 0.3.6)
├── login.py              # Login inicial — rodar UMA VEZ no Mac
├── report.py             # Gera PDFs (reportlab + matplotlib)
├── send_email.py         # Envia PDFs por SMTP (stdlib)
├── requirements.txt      # Dependências fixadas
├── garmin/
│   └── history.json      # Banco de dados persistido no git
├── reports/              # PDFs gerados (não commitados)
├── tests/
│   └── mock_history.json
└── .github/workflows/
    └── sync.yml
```

---

## Notas de segurança

- Tokens jamais aparecem em commits, logs ou artifacts
- `printf '%s'` usado para restaurar secrets (nunca `echo`)
- Hashes SHA-256 usados na comparação de tokens (nunca imprime conteúdo nos logs)
- `auth_failed.flag` sinaliza falha de autenticação sem vazar detalhes
