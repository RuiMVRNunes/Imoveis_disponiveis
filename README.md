# 🏠 Casa Radar

Radar pessoal de imóveis para portais portugueses. De hora a hora:

1. Varre **Idealista, Imovirtual, Supercasa e Custojusto** (Casa SAPO pronto, mas desligado).
2. Deteta **anúncios novos** e **baixas de preço** que batem com as tuas pesquisas.
3. Notifica por **email + WhatsApp** (e Telegram, opcional).
4. Regenera um **dashboard** no GitHub Pages e envia um **resumo diário**.
5. Corre **de graça** no GitHub Actions, sem servidores nem manutenção.

**Regra de ouro do sistema: nunca perder um anúncio novo.** Na dúvida entre filtrar e mostrar, mostra — antes um duplicado do que uma oportunidade perdida.

> ⚠️ **Nota legal:** fazer scraping destes portais viola os Termos de Serviço deles.
> Este projeto destina-se a **uso estritamente pessoal, não-comercial e de baixo volume**
> (1 corrida/hora, 2 páginas por fonte, com delays). Usa com bom senso.

---

## Como funciona

```
config.yaml  ──►  runner (hora a hora)
                    │  scrape por fonte (isolado: uma fonte a falhar não parte as outras)
                    │  filtros tolerantes (campo desconhecido nunca exclui)
                    │  dedup em 3 camadas (ID nativo → hash → fingerprint cross-portal)
                    ▼
                 state.json (o que já foi visto; commitado de volta ao repo)
                    │
                    ├──► notificações (email / WhatsApp / Telegram)
                    └──► docs/index.html (dashboard no GitHub Pages)
```

- **Primeira corrida = baseline:** regista tudo o que já existe **sem notificar**
  (assume-se que já viste os anúncios de hoje 😄) e envia uma única mensagem de
  confirmação. Só a partir da segunda corrida é que há alertas.
- **Silêncio nunca é ambíguo:** recebes alerta quando há novidades, um resumo diário
  **sempre** (mesmo com zero novos), e um alerta ativo se uma fonte der 0 resultados
  em 3 corridas seguidas (bloqueio ou parser partido). Esse alerta ativo dispara no
  máximo 1x/24h por fonte — o estado contínuo vive no resumo diário.
- **Horas de silêncio (0h–7h por defeito):** alertas encontrados de madrugada não
  tocam no telemóvel; acumulam e chegam juntos na primeira corrida da manhã.
  Configurável em `runtime.quiet_hours` (`[0, 0]` desliga).
- **Baixas de preço:** só notifica descidas **≥ 1%** (`min_price_drop_pct`);
  descidas menores atualizam o preço em silêncio.
- **Sem preço / "sob consulta":** excluído por decisão explícita (os restantes campos
  em falta continuam a passar — regra de ouro).
- **Anúncios desaparecidos:** uma vez por dia (à hora do resumo) o sistema verifica
  os URLs dos anúncios que segue (máx. 40/dia, com delays); os que dão 404/410
  aparecem no resumo diário como *"desapareceram do mercado"*, com os dias que
  estiveram anunciados. (Idealista fica de fora — o DataDome tornaria o teste inútil.)

---

## Setup passo-a-passo (do zero ao a-correr)

### 1. Criar o repositório

1. Cria um repositório **público** no GitHub (Actions ilimitado + Pages grátis; os
   segredos ficam em GitHub Secrets, nunca expostos no código).
2. Faz push deste código:

```bash
git init
git add .
git commit -m "Casa Radar"
git branch -M main
git remote add origin https://github.com/<o-teu-user>/<o-teu-repo>.git
git push -u origin main
```

### 2. Configurar as pesquisas

Tudo vive no **`config.yaml`** na raiz do repo — editas o ficheiro, a corrida seguinte
respeita a mudança. Nunca precisas de tocar no código.

Cada pesquisa aceita **duas formas, misturáveis**:

- **Filtros estruturados** (`locations`, `price_max`, `typologies`, ...) — o sistema
  constrói os URLs de cada portal.
- **`start_urls` colados** — a forma **mais fiável**: vai ao portal no browser, aplica
  os filtros todos à mão (piso, garagem, elevador, "com fotos", excluir leilões...),
  **ordena por mais recentes**, copia o URL da barra de endereço e cola no campo da
  fonte. Tem prioridade sobre os filtros estruturados e o scraper só trata da paginação.

```yaml
searches:
  - name: "Casa Feira"
    operation: buy
    locations: ["Santa Maria da Feira", "Fiães", "Lourosa"]
    price_max: 350000
    typologies: ["T3", "T4"]
    min_area_m2: 100
    keywords_exclude: ["trespasse", "leilão", "penhora"]
    sources: ["idealista", "imovirtual", "supercasa", "custojusto"]
    start_urls:
      idealista: "https://www.idealista.pt/comprar-casas/santa-maria-da-feira/com-preco-max_350000,t3,t4/?ordenado-por=data-publicacao-desc"
```

Campos úteis:

- `property_types: ["moradia"]` — só moradias (ou `["apartamento"]`; sem o campo, ambos).
- `price_min` / `price_max`, `typologies`, `min_area_m2` — filtros estruturados.

**Regra importante:** quando uma fonte tem `start_url`, o URL colado **é** o filtro —
os filtros estruturados de preço/tipologia/área **não se aplicam a essa fonte** (para
o YAML nunca cortar às escondidas o que o teu URL pediu). `keywords_exclude`,
`property_types` e a guarda compra/arrendamento aplicam-se sempre, em todas as fontes.

Se o config tiver um erro (indentação, campo mal escrito), o sistema **avisa qual a
pesquisa/campo** com problema nos logs e no resumo diário, e continua com as
pesquisas válidas — nunca rebenta em silêncio.

> 💡 **O repositório mexe sozinho:** cada corrida do Actions commita `state.json` e
> `docs/` de volta ao repo. Antes de editares localmente, faz sempre `git pull`
> (ou edita o `config.yaml` diretamente no site do GitHub, que é mais simples).

### 3. GitHub Secrets

No repo: **Settings → Secrets and variables → Actions → New repository secret**.
Cria estes (só os dos canais que vais usar):

| Secret | O que é | Exemplo |
|---|---|---|
| `SMTP_USER` | o teu Gmail | `eu@gmail.com` |
| `SMTP_PASS` | App Password do Gmail (passo 4) | `abcd efgh ijkl mnop` |
| `EMAIL_TO` | para onde recebes os alertas | `eu@gmail.com` |
| `CALLMEBOT_PHONE` | o teu número WhatsApp, formato internacional | `+351912345678` |
| `CALLMEBOT_APIKEY` | apikey do CallMeBot (passo 5) | `123456` |
| `TELEGRAM_TOKEN` | token do bot (passo 6, opcional) | `1234:AAxx...` |
| `TELEGRAM_CHAT_ID` | id do teu chat (passo 6, opcional) | `987654321` |
| `RAPIDAPI_KEY` | key RapidAPI por defeito (idealista via API, passo 5b) | `7ffb...ffd9e` |
| `RAPIDAPI_KEY_1..6` | um token por concelho (opcional, passo 5b) | `fac8...5de0` |

(`SMTP_HOST` e `SMTP_PORT` também existem mas são opcionais — sem eles assume-se
Gmail: `smtp.gmail.com`, porta `465`. Só precisas deles se usares outro provedor.)

Passo a passo no site do GitHub:
1. Abre o repo → separador **Settings** (o último, à direita).
2. No menu lateral esquerdo, em "Security": **Secrets and variables → Actions**.
3. Botão verde **New repository secret**.
4. Em *Name* mete o nome exato da tabela (ex.: `SMTP_USER`), em *Secret* o valor. **Add secret**.
5. Repete para cada um.

### 4. Gmail App Password

O Gmail não aceita a tua password normal em scripts. Precisas de uma **App Password**:

1. Ativa a **verificação em 2 passos** na conta Google: <https://myaccount.google.com/security>.
2. Vai a <https://myaccount.google.com/apppasswords>.
3. Em "App name" escreve `Casa Radar` → **Create**.
4. Copia a password de 16 letras (com ou sem espaços, tanto faz) → é o teu `SMTP_PASS`.

### 5. WhatsApp via CallMeBot (one-time)

1. Guarda o número **+34 611 01 16 37** nos contactos do telemóvel (ex.: "CallMeBot").
   ⚠️ O CallMeBot muda de número de tempos a tempos — se não responder, confirma o
   número atual em <https://www.callmebot.com/blog/free-api-whatsapp-messages/>.
2. Manda-lhe pelo WhatsApp a mensagem exata: `I allow callmebot to send me messages`.
3. A resposta com a tua **apikey** chega em ~2 minutos. Se não chegar, o próprio
   CallMeBot manda esperar 24h antes de tentar outra vez (o serviço é gratuito e
   às vezes satura) — mais uma razão para teres também o Telegram (passo 6).
4. `CALLMEBOT_PHONE` = o teu número com indicativo (`+3519...`), `CALLMEBOT_APIKEY` = a apikey.

> O CallMeBot é um serviço gratuito de terceiros — às vezes está lento ou em baixo.
> Por isso o **Telegram é recomendado como canal mais fiável** (passo 6); podes ter os dois.

### 5b. idealista via API (RapidAPI idealista17)

O idealista bloqueia scraping a partir da cloud (DataDome). A solução é a API
**idealista17** na RapidAPI, que aceita o teu URL de pesquisa do idealista.pt e
devolve os resultados (com fotos) — funciona no GitHub Actions e integra-se no
radar como as outras fontes.

1. Cria conta em <https://rapidapi.com>, procura **"idealista17"** e subscreve o
   **plano grátis**. Aponta o **limite de pedidos/mês** que aparece.
2. Em **Security → App Key** copia a tua key.
3. No `config.yaml`, mete `idealista_api` nas `sources` da pesquisa e lista os
   teus URLs de pesquisa do idealista.pt em **`idealista_urls`**, um por concelho,
   cada um com a sua key: `{ url: "...", key: RAPIDAPI_KEY_1 }`.
4. **Um token por concelho** (é a chave de tudo). ⚠️ A API **não aceita URLs de
   polígono** (`/areas/?shape=`) — só URLs normais por localidade. O plano grátis
   dá **100 pedidos/mês por token**, por isso usa uma conta RapidAPI (um token)
   por concelho, e cria um secret por cada: `RAPIDAPI_KEY_1`, `RAPIDAPI_KEY_2`, …
5. **Quota:** com `idealista_run_hours: [8, 14, 20]`, cada token faz **1 pedido
   por janela** → cada concelho corre 3×/dia (~90/mês, dentro dos 100) e é revisto
   a cada ~6h. `rapidapi_monthly_cap: 95` é a trava de segurança **por token**.
   - Se dois concelhos partilharem o mesmo token, esse token **roda** entre eles
     (1 por janela). Com um só token para os 6, cada concelho seria visto ~a cada 2 dias.
6. Depois de adicionares `idealista_api` a uma pesquisa que **já tem baseline**,
   corre o workflow com **baseline** marcado uma vez — senão os anúncios atuais
   do idealista chegam todos de rajada como "novos".

### 6. Telegram (opcional, recomendado)

1. No Telegram, fala com **@BotFather** → `/newbot` → dá-lhe um nome → recebes o **token**.
2. Abre conversa com o teu bot novo e manda-lhe qualquer mensagem (ex.: "olá").
3. Abre no browser: `https://api.telegram.org/bot<TOKEN>/getUpdates` → procura
   `"chat":{"id":123456789` → esse número é o teu `TELEGRAM_CHAT_ID`.
4. No `config.yaml`, muda `telegram: { enabled: false }` para `true`.

### 7. Ativar o GitHub Pages (dashboard)

1. Repo → **Settings → Pages**.
2. Em "Build and deployment": *Source* = **Deploy from a branch**.
3. *Branch* = `main` (ou o teu branch principal), *Folder* = **`/docs`** → **Save**.
4. O dashboard fica em `https://<o-teu-user>.github.io/<o-teu-repo>/` uns minutos depois.
5. (Opcional mas recomendado) cola esse URL em `runtime.dashboard_url` no `config.yaml`
   para os alertas trazerem o link.

### 8. Ativar o workflow e correr o baseline

1. Repo → separador **Actions** → se pedir, clica **"I understand my workflows, enable them"**.
2. No menu esquerdo escolhe **Casa Radar** → botão **Run workflow**.
3. Primeiro, valida os canais: marca **test_notify** → Run. Deves receber uma mensagem
   de teste em cada canal ativo.
4. Depois corre outra vez **sem opções** → a primeira corrida cria o baseline e envia
   a mensagem *"Casa Radar ativo ✅ — baseline criado: N anúncios..."*.
5. A partir daí corre sozinho de hora a hora. (Nota: o cron do GitHub pode atrasar
   5–20 min e ocasionalmente saltar — para uso pessoal é irrelevante.)

Se um dia mudares muito as pesquisas e quiseres reconstruir o baseline sem levar uma
enxurrada de alertas: **Run workflow** com **baseline** marcado. Isto também **limpa
o histórico de eventos do dashboard** — é o botão de "recomeçar limpo" depois de
mexeres a sério no config.

### 9. Validar

- ✅ Mensagem de teste chegou por cada canal (`--test-notify`).
- ✅ Mensagem de baseline chegou com o total de anúncios.
- ✅ Dashboard abre e mostra as fontes e as métricas.
- ✅ No separador **Actions**, a corrida aparece a verde e o commit
  `chore: update state [skip ci]` aparece no histórico.

### 10. Alternativa: correr em casa (Raspberry Pi / PC / VM)

O GitHub Actions usa IPs de datacenter, que o **Idealista (DataDome) bloqueia**.
Um IP residencial resolve isso. Duas opções:

**a) Docker Compose (recomendado):**

```bash
cp .env.example .env      # preenche os segredos
echo {} > state.json      # ficheiro de estado inicial (senão o Docker cria uma pasta)
docker compose up -d      # corre 1x/hora num loop
```

**b) cron + Python direto:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium --with-deps
crontab -e   # e acrescenta:
# 0 * * * * cd /caminho/para/o/repo && .venv/bin/python main.py --once >> radar.log 2>&1
```

Nesse cenário o dashboard local fica em `docs/index.html`; se quiseres mantê-lo no
GitHub Pages, faz `git push` do `state.json` + `docs/` no fim de cada corrida (ou usa
um self-hosted runner do Actions em casa — melhor dos dois mundos).

### 11. Troubleshooting

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| **Idealista sempre a 0 / alerta de bloqueio** | DataDome bloqueia IPs de datacenter (GitHub Actions) | É esperado. Cola um `start_url` na mesma (tenta-se sempre), mas para Idealista fiável corre em casa (passo 10) |
| **Supercasa 403** | Anti-bot no primeiro pedido | Igual ao anterior: em casa costuma passar; o alerta de bloqueio avisa-te quando parar de dar |
| **"0 resultados há 3h"** numa fonte que dava | Site mudou o HTML (parser partido) ou bloqueio novo | Abre os logs (abaixo) e vê o warning; se o layout mudou, é ajustar seletores no ficheiro da fonte |
| **Não chega o resumo diário** | Morreu tudo (workflow desativado, secrets errados) | Actions → vê a última corrida. O GitHub desativa crons após 60 dias sem commits — faz um commit qualquer para reativar |
| **Email não chega** | App Password errada / 2FA desligado | Refaz o passo 4; testa com workflow `test_notify` |
| **WhatsApp não chega** | apikey errada ou CallMeBot em baixo | Refaz o passo 5; considera Telegram |
| **Dashboard desatualizado** | Pages não configurado no `/docs` ou corrida sem commits | Confirma passo 7 e vê se há commits `chore: update state` |

**Como ler os logs de uma corrida:** repo → **Actions** → workflow "Casa Radar" →
clica na corrida → job **radar** → passo **Run radar**. Cada fonte loga
`X vistos, Y após filtros` e os erros aparecem a vermelho com a causa
(`HTTP 403`, `captcha DataDome`, timeout...).

---

## CLI

```bash
python main.py                 # uma corrida (default; é o que o cron chama)
python main.py --dry-run       # mostra o que notificaria; não envia nem grava nada
python main.py --baseline      # reconstrói o baseline (regista tudo, sem alertas)
python main.py --test-notify   # mensagem de teste por todos os canais ativos
python main.py --source idealista   # corre só uma fonte (debug)
python main.py --log-level DEBUG    # logging detalhado
```

## Estrutura

```
casa_radar/
  sources/     # um plugin por portal (base.py + idealista, imovirtual, supercasa, custojusto, casasapo)
  notifiers/   # email, whatsapp (CallMeBot), telegram + composição das mensagens (PT)
  dashboard/   # gerador do docs/index.html (HTML+CSS puro, zero JS)
  core/        # models, config, state, dedup (3 camadas), filters, runner
main.py        # CLI
config.yaml    # as tuas pesquisas (dados, não código)
state.json     # o que já foi visto (criado na 1ª corrida, commitado pelo Actions)
docs/          # dashboard publicado no GitHub Pages
tests/         # testes com fixtures HTML guardadas — nunca batem nos sites reais
```

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```

55 testes cobrem parsers (com fixtures HTML, incluindo lixo de outras categorias),
dedup (3 camadas), filtros tolerantes (tipo de imóvel, guarda compra/arrendamento,
start_url como filtro), config inválido, estado, baseline, baixas de preço (com
limiar mínimo), horas de silêncio, agrupamento cross-portal, deteção de bloqueio
silencioso (com cooldown), anúncios desaparecidos e o dashboard.
