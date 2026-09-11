# Claude Usage Monitor (Terminal Edition)

Monitor de terminal para acompanhar seus limites de uso, projeção de consumo, tokens e sessões do **Claude Code** em tempo real no computador.

Inspirado no projeto [Claude Usage Stick](https://github.com/benevid/claude-usage-stick-SVGL) (gadget ESP32 com tela touch), mas adaptado para rodar como um dashboard leve no terminal, sem necessidade de hardware adicional.

---

## 📸 Recursos

- **Janela de 5 Horas**:
  - Porcentagem de utilização, medidor colorido (verde → amarelo → vermelho) e status (`ALLOWED`, `WARNING`, `REJECTED`).
  - **Contagem regressiva ao vivo** até o reset e horário local do reset.
  - **Projeção de ritmo**: calcula se a cota vai durar até o reset ou a que horas irá esgotar.

- **Janela Semanal (7 Dias)**:
  - Utilização acumulada da semana e prazo de reset, com marcadores na barra a cada 20%.
  - **Projeção semanal**: no ritmo atual, a quanto chega no reset ou quando esgota (a partir de 12h de janela).
  - **Meta**: quanto dá para usar por dia, em média, sem estourar antes do reset.
  - **Por dia**: quanto da cota semanal foi usado em cada dia (fatias de 24h a partir do início da janela). A status line e o monitor anotam o uso em `usage_history.jsonl`; dias sem anotação suficiente aparecem como `--`, e `≥` indica um mínimo (a anotação começou no meio do dia).
  - **Projetos**: fatia estimada de cada projeto no consumo local da semana, pelos transcripts (tokens ponderados pelas proporções de preço da API; não considera a diferença entre modelos nem o uso no claude.ai/celular).
  - Status da janela e status geral aparecem só como alerta (`ALLOWED_WARNING` ou `REJECTED`).

- **Tokens e Sessões do Claude Code**:
  - Lê os transcripts locais (`~/.claude/projects/**/*.jsonl`, incluindo subagentes) e mostra os tokens de entrada, saída e cache na janela de 5h.
  - Lista cada sessão aberta com o modelo, o contexto usado sobre a janela da sessão (ex.: `428k/1M`) e o estado: trabalhando (com cronômetro), aguardando você ou livre.
  - Alerta de contexto calibrado pela janela: amarelo a partir de 300k ou 50% da janela, vermelho a partir de 600k ou 80% (o que vier primeiro).

- **Saúde da API & Latência**:
  - De onde veio o uso exibido: status line do Claude Code (sem consulta à API) ou a própria API, com a latência da consulta.
  - Status de cada serviço (**Claude API**, **Claude Code**, **claude.ai**, **Claude Cowork**) lido do `status.claude.com` — público, sem token e sem gastar cota.
  - Incidentes em aberto com nome, serviço afetado e status (o mais grave, se houver vários).
  - O nome do incidente é traduzido para português pelo próprio Claude Code (`claude -p` com Haiku), uma única vez por incidente, e guardado em `translations.json`. Sem o Claude Code disponível, o nome aparece em inglês.

- **Integração com o Windows**:
  - **Notificações nativas com som**: tarefa concluída, Claude aguardando você, contexto grande numa sessão (sugere `/clear` ou `/compact`), limite de 5h resetado, uso acima de 80% (5h e semanal) e novos incidentes (com o nome do incidente).
  - **Ícone na bandeja do sistema**: ocultar/mostrar o terminal (duplo clique) e **Fixar no Topo**, que mantém o monitor visível mesmo após o Win+D.

- **Relatório Rápido (`--once`)**: exibe os limites uma única vez no terminal e sai.

---

## 🚀 Como Usar

Instale as dependências:
```bash
pip install -r requirements.txt
```

### Dashboard no Terminal
Dê um duplo clique no arquivo:
```cmd
run.bat
```
Ou pelo terminal:
```bash
python main.py
```

### Consultar o Uso Apenas Uma Vez
```bash
python main.py --once
```

### Rodar os Testes
```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```
Os testes usam pastas temporárias: não leem nem alteram `~/.claude` nem os dados reais do monitor.

---

## 📊 Status Line do Claude Code (recomendado)

O Claude Code passa para a [status line](https://code.claude.com/docs/en/statusline) o uso de 5h/7d do seu plano (Pro/Max) e a janela de contexto de cada sessão. O `statusline.py` grava esses dados para o monitor e mostra uma linha compacta no rodapé de cada sessão:

```
5h 17% · 7d 16% · ctx 428k/1M (43%)
```

Para ativar, adicione ao `~/.claude/settings.json` (use barras normais no caminho):

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:/caminho/para/claude-usage-monitor/statusline.py"
  }
}
```

Com ela ativa:
- O uso vem do próprio Claude Code, **sem consultar a API** e sem gastar cota. A API só é consultada quando nenhuma sessão local respondeu nos últimos 5 minutos.
- O alerta de contexto usa a janela exata de cada sessão (200k ou 1M).
- Uso feito pelo claude.ai ou pelo celular só aparece na próxima resposta de uma sessão local (a consulta à API, quando usada, enxerga a conta inteira na hora).
- Com uma status line personalizada, o Claude Code deixa de mostrar algumas dicas do rodapé, como "esc to interrupt" (os atalhos continuam funcionando).

---

## 🔑 Autenticação Automática

Você não precisa copiar nem colar tokens se já usa o Claude Code na sua máquina!

O aplicativo procura o token na seguinte ordem:
1. `manual_token` no arquivo `config.json` local (se preferir informar manualmente)
2. Variável de ambiente `CLAUDE_CODE_OAUTH_TOKEN`
3. `~/.claude/.credentials.json` (gerado após você logar no Claude Code)

O token é relido a cada consulta, então quando o Claude Code o renova o monitor passa a usar o novo automaticamente.

---

## ⚙️ Configurações (`config.json`)

Crie um `config.json` na pasta do projeto para personalizar:

```json
{
  "poll_interval_sec": 120,
  "manual_token": ""
}
```

- `poll_interval_sec`: Tempo em segundos entre cada consulta à API (padrão: 120s).
- `manual_token`: Token do Claude informado manualmente (opcional).

---

## 🧠 Como Funciona por Baixo dos Panos

1. **Fonte do Uso**:
   Primeiro o monitor procura, em `statusline_data/`, o uso gravado pela status line do Claude Code nos últimos 5 minutos. Entre várias sessões, vale a leitura mais recente (dentro de uma janela o uso só cresce, então o maior percentual é o mais novo). Sem dado recente, ele consulta a API (itens 2 e 3).

2. **Consulta Mínima à API** (reserva):
   O aplicativo envia uma requisição `POST` com `max_tokens: 1` para `https://api.anthropic.com/v1/messages` com o cabeçalho `anthropic-beta: oauth-2025-04-20` e o `User-Agent` do Claude Code.
   O corpo retornado é descartado — cada consulta consome apenas alguns tokens (cerca de 8 de entrada e 1 de saída).

3. **Leitura dos Cabeçalhos Unificados**:
   A Anthropic retorna o status e a utilização diretamente nos headers HTTP:
   - `anthropic-ratelimit-unified-5h-utilization`: uso da janela de 5 horas (0.0 a 1.0)
   - `anthropic-ratelimit-unified-5h-reset`: timestamp Unix do reset de 5 horas
   - `anthropic-ratelimit-unified-7d-utilization`: uso semanal (0.0 a 1.0)
   - `anthropic-ratelimit-unified-7d-reset`: timestamp Unix do reset semanal
   - `anthropic-ratelimit-unified-status`: `allowed`, `allowed_warning` ou `rejected`
   - `anthropic-ratelimit-unified-representative-claim`: `five_hour` ou `seven_day`

4. **Contagem Local de Tokens**:
   Para saber quantos tokens foram consumidos de fato, o aplicativo faz o parsing dos arquivos `.jsonl` em `~/.claude/projects/` (incluindo os transcripts de subagentes) desde o início da janela de 5h (`reset - 5 horas`), somando input, output e cache.

5. **Estado das Sessões**:
   O status de cada sessão aberta vem de `~/.claude/sessions/*.json`, lido a cada segundo, o que permite notificar assim que uma tarefa termina.
