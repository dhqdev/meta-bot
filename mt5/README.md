# MetaTrader 5 em Docker (Meta-Bot)

Imagem do MetaTrader 5 rodando em Linux com **Wine**, com a tela do terminal acessível pelo navegador (**KasmVNC**) e o **bridge HTTP do Meta-Bot**, que deixa o backend consultar cotações, histórico e enviar ordens.

> **Servidor ARM ou prefere Windows?** O MT5 não precisa rodar no servidor do Meta-Bot: veja o kit [`windows/`](windows/LEIA-ME.md) (MT5 num PC/VPS Windows) ou o [`deploy/mt5-remoto.yml`](../deploy/mt5-remoto.yml) (esta imagem numa máquina Linux Intel/AMD), ambos ligados ao servidor pelo Tailscale.

Baseada em [gmag11/MetaTrader5-Docker](https://github.com/gmag11/MetaTrader5-Docker) (licença MIT, ver `LICENSE-gmag11.md`). O que mudou em relação ao original:

| Original | Meta-Bot |
|---|---|
| Servidor RPyC (`mt5linux`) na porta 8001, **sem senha** (quem alcança a porta executa código no container) | Bridge HTTP/JSON na porta 8001 com **token obrigatório** (`MT5_BRIDGE_TOKEN`). O RPyC continua disponível com `ENABLE_MT5LINUX=true`, na porta 18812 |
| Sempre o instalador genérico da MetaQuotes | `MT5_INSTALLER_URL` aceita o instalador da sua corretora |
| Login manual pela tela | Login automático opcional (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`) ou pelo próprio Meta-Bot (Configurações → MetaTrader 5) |
| Painel na raiz `/` | Painel pode ficar em `https://trade.tekvosoft.com/mt5/` (variável `SUBFOLDER`), protegido pelo login do Meta-Bot |

## Dá para usar qualquer corretora?

**Sim, qualquer corretora que ofereça MetaTrader 5.** O terminal genérico da MetaQuotes conecta em qualquer servidor MT5 (forex, CFDs, cripto e também B3: XP, Clear, Rico, Genial, Modal, BTG etc., quando a corretora libera o MT5 para a sua conta). Existem três jeitos de apontar para a sua:

1. **Pela tela (mais simples):** abra `https://trade.tekvosoft.com/mt5/`, vá em *Arquivo → Abrir uma conta*, digite o nome da corretora, escolha o servidor e selecione *Conectar-se a uma conta de negociação existente*. O servidor fica salvo no volume `/config` e, dali em diante, o login pelo Meta-Bot funciona sem tela.
2. **Pelo Meta-Bot:** em *Configurações → MetaTrader 5*, informe número da conta, senha e servidor (exatamente como aparece no terminal, ex.: `XPMT5-DEMO`, `ICMarketsSC-Demo`). O backend manda o login pelo bridge.
3. **Instalador da corretora:** algumas corretoras distribuem o próprio MT5 com os servidores já configurados. Coloque o link do instalador em `MT5_INSTALLER_URL` **antes da primeira inicialização** (ou apague o volume para reinstalar).

Um terminal fica logado em **uma conta por vez**. Para operar em **várias corretoras ao mesmo tempo**, suba um container `mt5` por conta (ex.: `mt5_xp`, `mt5_icm`), cada um com seu volume e token, e cadastre cada um em *Configurações → Terminais*.

## Variáveis de ambiente

| Variável | Obrigatória | Para quê |
|---|---|---|
| `MT5_BRIDGE_TOKEN` | **sim** | Chave do bridge (16+ caracteres). A mesma vai no backend (`MB_MT5_BRIDGE_TOKEN`). Gere com `openssl rand -hex 32` |
| `CUSTOM_USER` / `PASSWORD` | recomendado | Usuário e senha do painel web (KasmVNC) |
| `SUBFOLDER` | com o frontend | `/mt5/` para o painel funcionar em `https://trade.tekvosoft.com/mt5/` |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | não | Login automático na inicialização |
| `MT5_INSTALLER_URL` | não | Instalador do MT5 (padrão: o genérico da MetaQuotes) |
| `MT5_CMD_OPTIONS` | não | Opções de linha de comando do terminal (ex.: `/config:C:\\meu.ini`) |
| `MT5_PYTHON_PKG_VERSION` | não | Versão do pacote `MetaTrader5` no Wine (padrão `5.0.36`, a testada no projeto original) |
| `WINE_PYTHON_URL` | não | Instalador do Python para Windows (padrão 3.9.13) |
| `ENABLE_MT5LINUX` | não | `true` liga o RPyC do `mt5linux` na porta 18812 (sem autenticação) |

## Primeira inicialização

Leva de 5 a 10 minutos: o container instala Mono, MetaTrader 5, Python para Windows e o pacote `MetaTrader5` dentro do volume `/config`. Nas próximas vezes, sobe em segundos. O terminal se atualiza sozinho, como no Windows.

Requisitos: host **x86_64/amd64** (não roda em ARM), ~4 GB de disco para a imagem e ~2 GB para o volume.

## API do bridge (porta 8001, rede interna)

Todas as rotas, menos `/ping`, exigem `Authorization: Bearer <MT5_BRIDGE_TOKEN>`.

| Método e rota | O que faz |
|---|---|
| `GET /ping` | Vivo? (sem token) |
| `GET /health` | Terminal inicializado/conectado, conta, versão e hora do último tick |
| `POST /login` | `{login, password, server}` troca a conta logada |
| `POST /initialize` | Reinicia a conexão com o terminal (aceita `login`, `password`, `server`, `path`) |
| `GET /account` · `GET /terminal` | Dados da conta e do terminal |
| `GET /symbols?q=` · `GET /symbol?name=` | Lista e detalhes dos ativos da corretora |
| `GET /tick?symbol=` | Última cotação |
| `GET /rates?symbol=&timeframe=H1&count=1000` | Candles (M1 a MN1) |
| `GET /rates/range?symbol=&timeframe=&from=&to=` | Candles por período (unix) |
| `GET /positions` · `GET /orders` · `GET /history/deals` | Posições, ordens pendentes e negócios |
| `POST /order/check` · `POST /order/send` | `{action:"deal", symbol, side:"buy"/"sell", volume, sl, tp, magic, comment}`; o modo de preenchimento (FOK/IOC/RETURN) é escolhido sozinho pelo que o ativo aceita |
| `POST /position/close` · `POST /position/modify` | Fecha (total/parcial) ou muda stop/alvo de uma posição |

## Rodar só o MT5 (teste)

```bash
docker build -t meta-bot-mt5 mt5/
docker run -d --name mt5 -p 3000:3000 -p 8001:8001 \
  -e MT5_BRIDGE_TOKEN=$(openssl rand -hex 32) -e CUSTOM_USER=eu -e PASSWORD=senha-forte \
  -v mt5_config:/config meta-bot-mt5
# painel: http://localhost:3000   bridge: curl -H "Authorization: Bearer <token>" localhost:8001/health
```

Em produção, **não publique a porta 8001**: o backend acessa o bridge pela rede interna do Docker.

## Licenças

- Projeto original: [MIT](LICENSE-gmag11.md) (Germán Martín).
- [KasmVNC](https://github.com/kasmtech/KasmVNC): GPLv2. [Imagem base da LinuxServer](https://github.com/linuxserver/docker-baseimage-kasmvnc): GPLv3.
- MetaTrader 5 é da MetaQuotes; o uso segue os termos dela e da sua corretora.
