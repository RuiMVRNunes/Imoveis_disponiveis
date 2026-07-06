# PROMPT — Casa Radar (versão final)

> **Para o agente:** És um engenheiro de software sénior. Vais construir-me este projeto **de raiz e completo**, ficheiro a ficheiro, pronto a correr. Faz o **máximo de trabalho possível de forma autónoma** — assume defaults sensatos, decide os trade-offs pela opção mais robusta e barata, e **não me bloqueies com perguntas antes de começar**. Só depois de entregares tudo é que me fazes a lista de perguntas da **Secção 14** (decisões que tomaste + coisas que eu provavelmente não pensei). Idioma: código, identifiers e comentários em **inglês**; README, mensagens de notificação e textos que eu vou ler em **português**.

---

## 1. Objetivo

Ferramenta pessoal **Casa Radar** que:

1. Monitoriza portais imobiliários portugueses **de hora a hora**.
2. Deteta **anúncios novos** (e baixas de preço) que batem com as minhas pesquisas.
3. Notifica-me na hora por **email + WhatsApp**.
4. Dá-me um **dashboard visual** e um **resumo diário** para eu ter noção do que houve e do que não houve.
5. Corre **de graça**, sem manutenção.

Regra de ouro: **nunca perder um anúncio novo.** Em dúvida entre filtrar e mostrar → mostra. Prefiro um duplicado a uma oportunidade perdida.

---

## 2. Configuração das pesquisas (dados, não código)

Toda a definição das pesquisas vive num `config.yaml` que o sistema lê **no início de cada corrida**. Eu edito o ficheiro, a corrida seguinte respeita a mudança — **nunca mexo no código** para mudar uma pesquisa.

Suporta **múltiplas pesquisas em simultâneo**, cada uma independente e com `name`. Cada pesquisa aceita **duas formas** (misturáveis):

- **Filtros estruturados** — o sistema constrói o URL de cada portal a partir deles.
- **`start_urls` colados** — eu vou ao portal no browser, meto os filtros à mão (incluindo os finos: piso, garagem, elevador, "com fotos", excluir leilões...), copio o URL e colo. O scraper usa-o como ponto de partida e trata só da paginação. **Esta forma tem prioridade quando presente.**

Exemplo a entregar (preenchido com placeholders realistas para a zona Feira/Aveiro):

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
      idealista: "COLA_AQUI_O_URL_JA_FILTRADO"   # opcional, tem prioridade

  - name: "Arrendamento Aveiro"
    operation: rent
    locations: ["Aveiro"]
    price_max: 900
    typologies: ["T2"]
    sources: ["idealista", "imovirtual"]

notifications:
  email:    { enabled: true,  to: "eu@exemplo.com" }
  whatsapp: { enabled: true,  provider: callmebot }
  telegram: { enabled: false }

runtime:
  max_pages_per_source: 2
  request_delay_seconds: [2, 6]
  silent_block_threshold: 3      # corridas seguidas com 0 vistos → alerta
  daily_digest_hour: 22          # hora do resumo diário
  timezone: "Europe/Lisbon"
  notify_price_drops: true
```

**Validação no arranque:** se o `config.yaml` tiver erro (indentação, campo mal escrito), avisa-me qual a pesquisa/campo com problema e continua com as pesquisas válidas — nunca rebentar em silêncio. Segredos (SMTP, CallMeBot, Telegram) **nunca** no YAML; só em variáveis de ambiente / GitHub Secrets.

---

## 3. Fontes (arquitetura de plugins)

Cada portal é um módulo independente que implementa a mesma interface. Uma fonte a falhar **nunca** pode partir as outras. Ligadas por defeito: **Idealista, Imovirtual, Supercasa, Custojusto** (Casa SAPO desativado por defeito — partilha backend com o Supercasa; deixa o plugin pronto mas off).

```python
class SourceScraper(Protocol):
    name: str
    def search(self, search_config) -> list[Listing]: ...
    def is_enabled(self) -> bool: ...
```

`Listing` (dataclass tipada): `id`, `source`, `search_name`, `title`, `price` (int, EUR), `location`, `area_m2`, `rooms`, `url` (canónico), `image_url`, `published_at`, `raw`.

---

## 4. Estratégia de scraping / anti-bot

Realidade a assumir, não a esconder: o Idealista usa DataDome e bloqueia IPs de datacenter. Implementa em camadas, **por fonte**:

- **Nível 1 — HTTP:** `httpx` + `selectolax`/BeautifulSoup para os portais leves (supercasa, imovirtual, custojusto). Headers realistas, `Accept-Language: pt-PT`, User-Agent rotativo, `Referer` coerente.
- **Nível 2 — Browser:** `playwright` (chromium) + stealth (esconder `navigator.webdriver`, viewport realista, delays humanos) para o Idealista e outros protegidos. Reutiliza cookies/contexto entre corridas.
- **Respeito:** delays aleatórios (`request_delay_seconds`), no máximo `max_pages_per_source` páginas por fonte (só preciso dos mais recentes), 1 corrida/hora.
- **Parsing tolerante:** se um campo secundário falhar, warning mas não deites o anúncio fora.

No README, nota curta: fazer scraping destes portais viola os ToS; isto é uso estritamente pessoal, não-comercial, baixo volume.

---

## 5. Deduplicação (3 camadas) + preço + re-listagem

1. **ID nativo do portal** (extraído do URL por regex, ex. `idealista:33445566`). Chave primária de dedup dentro do mesmo portal. Resolve ~95%.
2. **Fallback por hash:** quando não há ID limpo, `sha1(source + url_canónico)`. O URL canónico é limpo de parâmetros de tracking (`utm_*`, `rank`, etc.) **antes** de hashar.
3. **Fingerprint de propriedade (cross-source):** o mesmo imóvel aparece em vários portais com IDs diferentes. Gera `normaliza(concelho) + tipologia + arredonda(área,5) + faixa_preço` e compara com tolerância (área ±3m², preço ±2%). Se bate → é o **mesmo** imóvel: notifica **uma vez** e agrupa os vários URLs. Se houver GPS, usa match por distância.

- **Baixas de preço:** mesmo ID, preço menor → não é "novo", mas notifica como *baixa de preço* (guarda `last_price`). Controlado por `notify_price_drops`.
- **Re-listagem:** anúncio apagado e reposto ganha ID novo → será notificado como novo. Aceitável (regra de ouro).

Estado em **`state.json`**: `{ chave: { first_seen, last_price, fingerprint, urls: [...] } }`.

---

## 6. ⭐ Primeira corrida = modo BASELINE (crítico)

Na **primeiríssima execução** (state vazio), o sistema faz **scan completo de tudo o que está listado, grava no `state.json` como "já visto", e NÃO envia notificações de anúncios**. Assume-se que tudo o que existe agora já foi visto (a minha mulher já viu os anúncios todos de hoje 😄). No fim do baseline, envia **uma única** mensagem de confirmação:

> *"Casa Radar ativo ✅ — baseline criado: 312 anúncios registados em 2 pesquisas, 4 fontes OK. A partir daqui só recebes o que for novo."*

Só a partir da **segunda corrida** é que anúncios não vistos disparam alertas. Flag `--baseline` para eu poder reconstruir o baseline de propósito (ex. depois de mudar muito as pesquisas) sem levar uma enxurrada.

---

## 7. Notificações

Digest por corrida (não spam anúncio-a-anúncio, exceto se ≤3 novos). Cada alerta diz **de que pesquisa** veio ("2 novos em 'Casa Feira'").

- **Email:** SMTP Gmail (App Password). HTML com cards: foto, preço, localização, tipologia, área, badge da fonte, botão "Ver anúncio". Fallback texto.
- **WhatsApp:** CallMeBot (setup one-time documentado no README). Mensagem compacta com título, preço, localização, URL.
- **Telegram:** alternativa robusta (BotFather + `sendMessage` Markdown). Recomendar no README como o canal mais fiável; manter WhatsApp ativo.

Canais ligáveis independentemente. Se um falhar, os outros continuam. Comando **`--test-notify`** envia uma mensagem de teste por todos os canais ativos (para eu validar o setup).

---

## 8. Controlo do que houve / não houve (observabilidade)

Três sinais independentes para que o silêncio nunca seja ambíguo:

1. **Alerta de oportunidade** → só quando há novo (o que quero receber).
2. **Resumo diário / heartbeat** (à `daily_digest_hour`) → chega **sempre**, mesmo com zero novos, com o estado **por fonte**: *"24 corridas · imovirtual ✅ · supercasa ✅ · custojusto ✅ · idealista ⚠️ 0 desde 14h"*. Se não chega, é porque morreu tudo — a ausência é o próprio alarme.
3. **Deteção de bloqueio silencioso:** distinguir **0 vistos** (falha/bloqueio) de **0 novos** (dia calmo). Se uma fonte der **0 vistos** `silent_block_threshold` corridas seguidas → alerta ativo: *"⚠️ Idealista: 0 resultados há 3h, provável bloqueio ou mudança no site."* Apanha tanto bloqueio DataDome como parser partido (ambos = 0 vistos).

Logging estruturado com resumo por corrida (por fonte: nº vistos, nº novos, tempo, erros).

---

## 9. Dashboard estático (GitHub Pages, grátis)

A cada corrida, além de notificar, **regenera um `docs/index.html`** (servido pelo GitHub Pages). Read-only, tão fresco quanto a última corrida. Layout:

- **Topo:** estado (ativo/última/próxima corrida).
- **Cards de métricas:** corridas hoje, anúncios vistos, novos hoje, baixas de preço.
- **Estado das fontes:** um cartão por fonte, **verde** se OK, **amarelo** se em bloqueio silencioso (com "0 há Xh").
- **Atividade 24h:** mini-gráfico de barras (novos por hora).
- **"Apareceu hoje":** cards dos anúncios novos (foto, preço, localização, tipologia, fonte, "há X min", badge "novo" ou baixa de preço a verde), link para o anúncio.

Design limpo, sóbrio, mobile-friendly, sem dependências pesadas (HTML+CSS gerado; JS mínimo). Guardar histórico suficiente para as vistas de "hoje" e "24h" (o resto configurável — ver Secção 14).

---

## 10. Agendamento e hosting (grátis)

**Recomendado — GitHub Actions em repo público** (Actions ilimitado + Pages grátis; segredos ficam em GitHub Secrets, nunca expostos):

- Workflow `.github/workflows/radar.yml`: `schedule: cron: '0 * * * *'` + `workflow_dispatch` (correr à mão).
- **Persistência de estado:** no fim, commitar `state.json` (e `docs/`) de volta ao repo com `chore: update state [skip ci]` usando o `GITHUB_TOKEN`.
- Nota no README: cron do GitHub pode atrasar 5–20 min e não é 100% pontual — ok para uso pessoal.

**Alternativa — runner em casa (Raspberry/PC) ou Oracle Always Free VM:** cron/systemd timer. IP residencial (casa) resolve o bloqueio do Idealista. Entregar `Dockerfile` + `docker-compose.yml` opcionais.

---

## 11. Instruções de setup (README passo-a-passo, em português)

O README tem de me levar do zero ao a-correr sem eu adivinhar nada. Inclui, com detalhe:

1. **Criar o repo** (público) e fazer push do código.
2. **Configurar as pesquisas:** onde está o `config.yaml`, como colar os `start_urls` do browser.
3. **GitHub Secrets:** exatamente que secrets criar e onde (`SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`, `CALLMEBOT_PHONE`, `CALLMEBOT_APIKEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`), com screenshots-descritos passo a passo (Settings → Secrets and variables → Actions).
4. **Gmail App Password:** como gerar (2FA + App Passwords).
5. **CallMeBot:** o processo de registo (mandar a mensagem ao número deles para obter a apikey).
6. **Telegram (opcional):** criar bot no BotFather, obter `chat_id`.
7. **Ativar GitHub Pages:** Settings → Pages → source `docs/`.
8. **Ativar o workflow** e correr o **baseline** à mão via `workflow_dispatch`.
9. **Validar:** correr `--test-notify`; confirmar a mensagem de baseline; abrir o dashboard.
10. **Alternativa Raspberry/VM:** como correr via cron/self-hosted runner.
11. Secção de **troubleshooting** (Idealista a dar 0 → provável IP datacenter; como ler os logs de uma corrida no separador Actions).

---

## 12. CLI e robustez

- Flags: `--once` (default, para cron), `--dry-run` (mostra o que notificaria, não envia nem grava), `--test-notify`, `--baseline`, `--source X` (correr só uma fonte).
- Isolamento por fonte (try/except à volta de cada scraper).
- Retries com backoff exponencial em falhas de rede.
- Logging estruturado, nível configurável.

---

## 13. Entregáveis

- Repo completo e modular:
  ```
  casa_radar/
    sources/     # base.py + idealista.py, imovirtual.py, supercasa.py, custojusto.py, casasapo.py
    notifiers/   # base.py + email.py, whatsapp.py, telegram.py
    dashboard/   # gerador do docs/index.html
    core/        # models.py, state.py, dedup.py, filters.py, runner.py, config.py
    config.yaml  # exemplo preenchido
    main.py      # CLI
  .github/workflows/radar.yml
  docs/          # dashboard gerado (GitHub Pages)
  tests/         # testes de parser e de dedup com fixtures HTML guardadas
  Dockerfile, docker-compose.yml
  .env.example
  requirements.txt / pyproject.toml
  README.md      # em português, passo-a-passo (Secção 11)
  ```
- Testes que **não** batem nos sites reais (fixtures HTML guardadas).
- Python 3.11+, tipado, dependências mínimas, sem serviços pagos, sem DB gerida.

---

## 14. ⭐ No fim: faz-me perguntas (obrigatório)

Depois de entregares tudo a funcionar, **não termines em silêncio**. Escreve-me duas listas curtas:

**A) Decisões que tomei por ti** — cada default não-óbvio que assumiste, em 1 linha, para eu confirmar ou mudar (ex.: tolerâncias do fingerprint, quantas páginas por fonte, quanto histórico guardar, formato das mensagens, se agrupo baixas de preço com novos ou em secção à parte).

**B) Perguntas sobre coisas que eu provavelmente não pensei** — levanta ativamente as decisões que um utilizador leigo não anteciparia, por exemplo (não te limites a estas):
- Quero **horas de silêncio** (ex. não receber WhatsApp entre 0h–7h, só juntar no digest da manhã)?
- Quanto **histórico** guardar no dashboard (só hoje? 30 dias? sempre, para ver tendências de preço por zona)?
- Quero um **limite máximo de anúncios por notificação** (para uma pesquisa nova não me despejar 50 de rajada mesmo fora do baseline)?
- Como tratar **imóveis "sob consulta"** / sem preço?
- Quero **filtrar por variação de preço mínima** (ignorar baixas de 500€ num imóvel de 300k)?
- Devo tentar **detetar quando um anúncio desaparece** (vendido/removido) e avisar-te?
- Quero **mais do que uma zona** desde já, ou começamos só com Feira e alargamos?
- **Fuso/DST**, moeda, formatação de datas — confirmar `Europe/Lisbon`.

Apresenta isto de forma numerada e objetiva, para eu responder e tu iterares.

---

**Começa agora. Entrega o repo completo, ficheiro a ficheiro, e só no fim me fazes as perguntas da Secção 14.**
