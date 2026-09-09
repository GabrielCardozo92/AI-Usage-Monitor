# Claude Usage Monitor (Desktop Edition)

Monitor desktop para acompanhar seus limites de uso, projeção de consumo e contagem de tokens do **Claude Code** em tempo real no computador.

Inspirado no projeto [Claude Usage Stick](https://github.com/benevid/claude-usage-stick-SVGL) (gadget ESP32 com tela touch), mas adaptado para rodar nativamente no Windows/macOS/Linux como um aplicativo Python leve, sem necessidade de hardware adicional.

---

## 📸 Recursos

- **Janela de 5 Horas**:
  - Porcentagem grande de utilização e status (`ALLOWED`, `WARNING`, `REJECTED`).
  - Medidor com **18 segmentos coloridos** (verde → âmbar → vermelho).
  - **Contagem regressiva ao vivo segundo a segundo** até o reset.
  - Horário exato do reset local.
  - **Projeção de ritmo inteligente**: calcula se a cota vai durar até o reset ou a que horas irá esgotar.

- **Janela Semanal (7 Dias)**:
  - Utilização acumulada da semana e prazo de reset.
  - Identificação do fator limitante principal (`claim: five_hour` ou `seven_day`).

- **Tokens Reais da Janela (Claude Code)**:
  - Lê automaticamente as mensagens dos transcripts locais (`~/.claude/projects/**/*.jsonl`).
  - Mostra tokens de entrada (input + cache write), saída (output) e leitura de cache.

- **Saúde da API & Latência**:
  - Sonda em segundo plano da latência real dos modelos: **Haiku**, **Sonnet** e **Opus**.
  - Monitoramento de incidentes ao vivo do `status.claude.com`.

- **Experiência Desktop**:
  - **Interface Gráfica Moderna (Tkinter)** com Dark Theme nativo do Windows e acentos na cor Coral do Claude (`#D97757`).
  - **📌 Fixar no Topo (Always on Top)**: mantenha o monitor flutuando sobre o editor ou terminal enquanto programa.
  - **🗗 Modo Mini-Widget**: visualização compacta e discreta para o cantinho da tela.
  - **Dashboard Terminal (Rich)**: modo TUI interativo para quem prefere o terminal.
  - **Relatório Rápido (`--once`)**: exibe os limites uma única vez no terminal e sai.

---

## 🚀 Como Usar

### 1. Iniciar a Interface Gráfica (Desktop)
Dê um duplo clique no arquivo:
```cmd
run.bat
```
Ou pelo terminal:
```bash
python main.py
```

### 2. Iniciar o Modo Mini-Widget
```bash
python main.py --mini
```

### 3. Iniciar o Modo Terminal (Rich Dashboard)
Dê um duplo clique em:
```cmd
run_cli.bat
```
Ou pelo terminal:
```bash
python main.py --cli
```

### 4. Consultar o Uso Apenas Uma Vez
```bash
python main.py --once
```

---

## 🔑 Autenticação Automática

Você não precisa copiar nem colar tokens se já usa o Claude Code na sua máquina!

O aplicativo detecta o token automaticamente na seguinte ordem:
1. `~/.claude/.credentials.json` (gerado após você logar no Claude Code)
2. Variável de ambiente `CLAUDE_CODE_OAUTH_TOKEN`
3. Arquivo `config.json` local (se preferir informar manualmente)

---

## ⚙️ Configurações (`config.json`)

Você pode personalizar o intervalo de atualização e o comportamento no arquivo `config.json`:

```json
{
  "poll_interval_sec": 120,
  "probe_models": true,
  "always_on_top": false,
  "mini_mode": false,
  "manual_token": ""
}
```

- `poll_interval_sec`: Tempo em segundos entre cada consulta à API (padrão: 120s).
- `probe_models`: Habilita ou desabilita os testes de latência dos modelos.
- `always_on_top`: Se a janela deve permanecer sempre no topo.

---

## 🧠 Como Funciona por Baixo dos Panos

1. **Consulta Mínima à API**:
   O aplicativo envia uma requisição `POST` com `max_tokens: 1` para `https://api.anthropic.com/v1/messages` com o cabeçalho `anthropic-beta: oauth-2025-04-20` e o `User-Agent` do Claude Code.
   O corpo retornado é descartado — **o consumo de quota da consulta é de apenas 1 token**.

2. **Leitura dos Cabeçalhos Unificados**:
   A Anthropic retorna o status e a utilização diretamente nos headers HTTP:
   - `anthropic-ratelimit-unified-5h-utilization`: uso da janela de 5 horas (0.0 a 1.0)
   - `anthropic-ratelimit-unified-5h-reset`: timestamp Unix do reset de 5 horas
   - `anthropic-ratelimit-unified-7d-utilization`: uso semanal (0.0 a 1.0)
   - `anthropic-ratelimit-unified-7d-reset`: timestamp Unix do reset semanal
   - `anthropic-ratelimit-unified-status`: `allowed`, `allowed_warning` ou `rejected`
   - `anthropic-ratelimit-unified-representative-claim`: `five_hour` ou `seven_day`

3. **Contagem Local de Tokens**:
   Para saber quantos tokens foram consumidos de fato, o aplicativo faz o parsing dos arquivos `.jsonl` em `~/.claude/projects/` desde o início da janela de 5h (`reset - 5 horas`), somando input, output e cache.
