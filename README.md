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
  - Utilização acumulada da semana, prazo de reset e status geral.
  - Identificação do fator limitante principal (`claim: five_hour` ou `seven_day`).

- **Tokens e Sessões do Claude Code**:
  - Lê os transcripts locais (`~/.claude/projects/**/*.jsonl`, incluindo subagentes) e mostra os tokens de entrada, saída e cache na janela de 5h.
  - Lista cada sessão aberta com o modelo, o tamanho do contexto (com alerta quando cresce demais) e o estado: trabalhando (com cronômetro), aguardando você ou livre.

- **Saúde da API & Latência**:
  - Latência da própria consulta de uso (Haiku), sem nenhuma requisição extra.
  - Status de cada serviço (**Claude API**, **Claude Code**, **claude.ai**) e incidentes em aberto, lidos do `status.claude.com` — público, sem token e sem gastar cota.

- **Integração com o Windows**:
  - **Notificações nativas com som**: tarefa concluída, limite de 5h resetado, uso acima de 80% e novos incidentes.
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

1. **Consulta Mínima à API**:
   O aplicativo envia uma requisição `POST` com `max_tokens: 1` para `https://api.anthropic.com/v1/messages` com o cabeçalho `anthropic-beta: oauth-2025-04-20` e o `User-Agent` do Claude Code.
   O corpo retornado é descartado — cada consulta consome apenas alguns tokens (cerca de 8 de entrada e 1 de saída).

2. **Leitura dos Cabeçalhos Unificados**:
   A Anthropic retorna o status e a utilização diretamente nos headers HTTP:
   - `anthropic-ratelimit-unified-5h-utilization`: uso da janela de 5 horas (0.0 a 1.0)
   - `anthropic-ratelimit-unified-5h-reset`: timestamp Unix do reset de 5 horas
   - `anthropic-ratelimit-unified-7d-utilization`: uso semanal (0.0 a 1.0)
   - `anthropic-ratelimit-unified-7d-reset`: timestamp Unix do reset semanal
   - `anthropic-ratelimit-unified-status`: `allowed`, `allowed_warning` ou `rejected`
   - `anthropic-ratelimit-unified-representative-claim`: `five_hour` ou `seven_day`

3. **Contagem Local de Tokens**:
   Para saber quantos tokens foram consumidos de fato, o aplicativo faz o parsing dos arquivos `.jsonl` em `~/.claude/projects/` (incluindo os transcripts de subagentes) desde o início da janela de 5h (`reset - 5 horas`), somando input, output e cache.

4. **Estado das Sessões**:
   O status de cada sessão aberta vem de `~/.claude/sessions/*.json`, lido a cada segundo, o que permite notificar assim que uma tarefa termina.
