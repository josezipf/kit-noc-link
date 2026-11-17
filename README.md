# Lab NOTO TI · Dashboards de links (Kit NOC e Kit Provedor)

Laboratório em Docker que gera dados realistas de monitoramento para construir, validar e
gravar os dashboards Grafana do **Kit NOC (MSP)** e do **Kit Provedor**.
Todos os nomes e dados são fictícios.

> **Produção:** para monitorar os equipamentos reais (no provedor ou no cliente), use a pasta
> [`producao/`](producao/LEIA-ME.md). Ela sobe Zabbix + Grafana prontos, com os templates e os dashboards
> dos kits, sem nada do laboratório: `./producao/producao.sh up --provedor --msp`.

| Componente | Versão |
|---|---|
| Zabbix server / web | 7.0 LTS (PostgreSQL 16) |
| Grafana | 12.3.0 |
| Plugin Zabbix (alexanderzobnin-zabbix-app) | 6.10.3 |
| Business Text (marcusolsson-dynamictext-panel) | 6.3.1 |

## O que vem no repositório e o que é entregue à parte

| Parte | Onde fica |
|---|---|
| Lab (scripts, containers, topologia, cenários) e o template NOTO - LINK INTERNET / VPN | neste repositório |
| Produção (`producao/`: Zabbix + Grafana prontos para os equipamentos reais) | neste repositório |
| **Dashboards** (JSON) | entregues à parte: importe no Grafana (*Dashboards → New → Import*) e escolha o datasource **Zabbix**. O lab não cria pastas nem dashboards: cada um importa os que comprou, onde quiser |
| **Templates do Kit Provedor** (pasta `Templates/`) | entregues à parte: coloque a pasta `Templates/` na raiz do projeto antes de `./lab.sh up --provedor` |

Sem os dashboards, o lab sobe normalmente e o Zabbix coleta tudo; o Grafana já vem com o plugin e o datasource Zabbix prontos.

## Instalar numa máquina nova (Ubuntu 24.04)

Testado numa VM Ubuntu 24.04 zerada (4 vCPU, 8 GB de RAM).

```bash
# 1. Docker Engine + Compose (repositório oficial da Docker)
sudo apt-get update && sudo apt-get install -y ca-certificates curl git python3
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER      # depois saia e entre de novo na sessão (ou: newgrp docker)

# 2. O lab
git clone https://github.com/josezipf/kit-noc-link.git
cd kit-noc-link
./lab.sh up --msp                  # Kit NOC (MSP)
```

* **VM ou servidor (acessar o Grafana de outro computador):** na primeira vez, suba com
  `LAB_BIND=0.0.0.0 ./lab.sh up --msp`. Se já subiu, troque `LAB_BIND=0.0.0.0` no arquivo `.env`
  e rode o `./lab.sh up` de novo. O `lab.sh` mostra o endereço com o IP da máquina. Sem isso, as
  portas ficam só em `127.0.0.1` (acesso apenas na própria máquina).
* **Disco:** reserve ~10 GB livres (imagens + banco). O instalador do Ubuntu costuma usar só metade
  do disco no LVM; para usar o resto: `sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv`.

## Subir o lab

```bash
./lab.sh up --msp        # só o Kit NOC (links de internet MSP)
./lab.sh up --provedor   # só o Kit Provedor (topologia de provedor com os templates NOTO)
./lab.sh up              # os dois (ou repete a escolha feita na última vez)
./lab.sh status    # containers, rede, itens, cenário de cada link e acessos
./lab.sh creds     # URLs e senhas (geradas no .env na primeira execução)
./lab.sh down      # para tudo, mantém o histórico
./lab.sh destroy   # apaga tudo, inclusive o histórico (pede confirmação: digite APAGAR)
./lab.sh snmpwalk <host> [OID]   # SNMP visto da rede do Zabbix
```

Requisitos: Linux com Docker Engine + Docker Compose v2 e python3. O lab escolhe sozinho
uma faixa `10.x` livre e portas livres (Zabbix a partir de 8280, Grafana a partir de 3300),
publicadas só em `127.0.0.1`. Para abrir na rede: `LAB_BIND=0.0.0.0 ./lab.sh up` na primeira vez.

## Topologia

```
   zabbix-server ── noc ─┬─ core (gerência 10.x.1.0/24: o Zabbix monitora tudo por aqui)
   (mesma rede)          │    BORDA-01 .21 · BNG-01 .22 · SW-AGREGACAO .23 · OLT-CENTRO .31 · OLT-NORTE .32
   (Kit Provedor)        │    SRV-DNS .41 · SRV-RADIUS .42
                         │
                         │   TRANSITO-A AS64601 ─┐ (BGP real, FRR)
                         │   TRANSITO-B AS64602 ─┼─ BORDA-01 AS64512 ── BNG-01 ── SW-AGREGACAO ─┬─ OLT-CENTRO (4 PONs, 32 ONUs)
                         │   IX-SP      AS64603 ─┘                                               └─ OLT-NORTE  (4 PONs, 32 ONUs)
                         │
                         └─ internet ─┬─ OPERADORA (ponta remota do tráfego)          (Kit NOC)
                                      ├─ FW-INDUSTRIA-A / FW-ESCOLA-C / FW-FILIAL-NORTE / FW-CLINICA-H  wan1
                                      └─ VPNs até a LAN de cada FW
```

* Os links MSP ficam em `msp/links.csv` e `msp/vpns.csv`; a rede do provedor em `provedor/topologia.json`.
* O kit que não foi escolhido não sobe nenhum container, e os hosts dele ficam desativados
  no Zabbix (o histórico é preservado).

## Kit Provedor

"Provedor Exemplo" (fictício) monitorado com os **templates NOTO da pasta `Templates/`**, do mesmo
jeito que o aluno faz na produção: em cada host vai a base (`NOTO TI - SNMP`) mais o template do
equipamento. O `./lab.sh up` importa `Templates/TODOS - Kit Provedor.yaml`.

| Host | O que é | Templates | De onde vêm os dados |
|---|---|---|---|
| BORDA-01 | roteador de borda | SNMP + Interfaces SNMP + BGP | FRR com **BGP de verdade** com TRANSITO-A, TRANSITO-B e IX-SP (230 prefixos); BGP4-MIB pelo próprio FRR |
| BNG-01 | concentrador | SNMP + Interfaces SNMP | enlaces com tráfego real (iperf3) |
| SW-AGREGACAO | switch de agregação | SNMP + Interfaces SNMP | enlaces com tráfego real |
| OLT-CENTRO, OLT-NORTE | OLTs | SNMP + **OLT Lab** + Interfaces SNMP | OLT simulada (OIDs fictícios, padrão NOTO TI de OLT) |
| SRV-DNS, SRV-RADIUS | servidores | Disponibilidade + Linux (agente) | agente Zabbix 2 de verdade |
| Zabbix server | o próprio | Saúde do Zabbix server | interno |

* **Sem OIDs de fabricante:** o lab usa só MIBs padrão (IF-MIB, BGP4-MIB, IP-FORWARD-MIB) e, nas
  OLTs, OIDs fictícios no número de empresa de documentação (32473). Cada aluno usa o template do
  fabricante dele, que entrega as mesmas chaves (`olt.*`), e os dashboards funcionam igual.
* **Descrição das interfaces:** no padrão `Nome | banda` (`TRANSITO A AS64601 | 1G`), igual ao que
  o aluno coloca no equipamento real. A banda alimenta o "% de uso" dos dashboards.
* **Tráfego:** cada enlace tem tráfego real (iperf3, MTU 9000), com o perfil de um provedor
  residencial: ~25% de madrugada e pico às 21h do "dia" do lab (1 hora). O uplink de cada OLT
  acompanha a soma do tráfego das ONUs simuladas.
* **Estado normal do lab:** 4 alertas de propósito para o dashboard ter o que mostrar: em cada
  OLT, uma ONU sem energia ou offline e uma com sinal RX baixo.
* **Itens que o lab desativa no host** (e por quê): "Rotas IPv4 na RIB" do BGP (OID proprietário
  Huawei; a borda do lab é Linux/FRR) e a descoberta de serviços systemd dos servidores (container
  sem systemd). O "Linux by Zabbix agent" sai do host "Zabbix server" (o container não roda agente).

### Cenários do Kit Provedor

```bash
./lab.sh fail transito-a              # fibra do trânsito A rompida: interface oper down e BGP cai em ~10 s
./lab.sh bgp transito-b down          # só a sessão BGP cai (o enlace continua no ar)
./lab.sh saturate transito-b          # tráfego do trânsito B acima do contratado (~112%)
./lab.sh degrade borda-bng loss 5     # perda/latência num enlace (tc netem)
./lab.sh fail olt-norte               # equipamento fora da rede: só "Sem resposta de ping" (cascata)
./lab.sh onu olt-centro 1.2 los       # ONU: los | energia | offline | auth | desativada | rx-baixo | rx-critico | normal
./lab.sh pon olt-norte 4 down         # PON inteira fora (todas as ONUs dela em LOS)
./lab.sh restore transito-a           # volta um alvo ao normal
./lab.sh reset                        # volta tudo (as 4 anomalias fixas continuam)
./lab.sh status                       # enlaces, BGP e ONUs por estado, lidos do Zabbix
```

Enlaces: `transito-a`, `transito-b`, `ix`, `borda-bng`, `bng-agregacao`, `uplink-olt-centro`,
`uplink-olt-norte`. Equipamentos: `borda-01`, `bng-01`, `sw-agregacao`, `olt-centro`, `olt-norte`,
`srv-dns`, `srv-radius`.

> **Como o lab simula fibra rompida e equipamento fora:** no Docker, as duas pontas de um enlace se
> ligam por uma bridge, então derrubar uma não chega à outra. E `ip link set down` deixaria a porta
> *admin down*, que a descoberta de interfaces ignora de propósito. O lab derruba a **outra ponta do
> cabo** (o veth par, no host, via container auxiliar com `--network host`): a interface fica *admin up*
> e *oper down*, como no equipamento real. Para "equipamento fora", o corte é no cabo de gerência.

> **Por que o lab força a descoberta a cada `up`:** recriar um container renumera as interfaces
> (ifIndex). Sem forçar, o Zabbix só corrigiria os nomes dos itens na próxima descoberta (1 h).

## Kit NOC (MSP)

Os 4 firewalls usam o template **NOTO - LINK INTERNET** importado **sem nenhuma alteração**
(`msp/templates/template_internet_vpn.yaml`; o `lab.sh` confere o MD5 antes de importar).
Só macros **de host** são ajustadas:

| Macro de host | Valor no lab |
|---|---|
| `{$INDEX}` | ifIndex da `wan1` (descoberto automaticamente a cada `up`) |
| `{$INTERFACE}` | `wan1` |
| `{$BANDA_WARN_DOWN}` / `{$BANDA_WARN_UP}` | 80% do contratado, em bps exatos |
| `{$OPERADORA}` / `{$LOCAL}` | só para os dashboards Kit NOC (não são do template), vêm de `msp/links.csv` |

### VPNs (template NOTO - VPN)
4 túneis fictícios (`msp/vpns.csv`) no template **NOTO - VPN**, do mesmo arquivo e também sem
alteração. O Zabbix pinga a LAN do cliente (`lan1` do FW, ex.: `192.168.10.1`) **através da WAN
do FW**: se o link cai, a VPN cai junto, como na vida real. Download/Upload do template vêm
desativados e continuam assim. Macros de host opcionais: `{$VPN_TIPO}` e `{$VPN_CRIPTO}`.

### Como descobrir o `{$INDEX}`

```bash
./lab.sh snmpwalk FW-INDUSTRIA-A IF-MIB::ifName
#   ifName.1 lo
#   ifName.2 wan1      <- {$INDEX} = 2
#   ifName.3 lan1
```

Em produção: `snmpwalk -v2c -c <community> <ip> IF-MIB::ifName` (ou `ifDescr`), ache a
interface WAN e use o número no fim do OID.

> **Atenção ao sufixo `M` nas macros de banda:** em expressões do Zabbix, `M` = 1024×1024.
> `{$BANDA_WARN_DOWN}=400M` dispara em ~419 Mbps. Para o valor exato use bps: `400000000`.

### Tráfego (iperf3)
Cada FW gera tráfego UDP contra a OPERADORA (a "internet"): download entra na `wan1`
(`net.if.in.linkprincipal`) e upload sai dela (`net.if.out.linkprincipal`). A taxa muda a cada
minuto conforme o perfil do link em `msp/links.csv`:

| Link | Contratado | Perfil | Comportamento |
|---|---|---|---|
| FW-INDUSTRIA-A | 500 Mbps | `comercial` | 10% de madrugada, pico ~68% no meio do dia |
| FW-ESCOLA-C | 200 Mbps | `limite` | 85 a 92% o tempo todo |
| FW-FILIAL-NORTE | 100 Mbps | `acima` | 106 a 114% (acima do contratado) |
| FW-CLINICA-H | 300 Mbps | `baixo` | 10 a 20% |

O "dia" do lab dura **1 hora** (`LAB_DIA_SEGUNDOS=3600` no `.env`), para a curva aparecer numa
gravação. Para horário real use `86400` e rode `./lab.sh up`.

### Cenários

```bash
./lab.sh degrade <link> loss <%> latency <ms>   # tc netem na wan1 do FW (pode usar só um)
./lab.sh saturate <link>                         # tráfego vai para ~112% do contratado
./lab.sh fail <link>                             # derruba a wan1
./lab.sh fail vpn-<cliente>                      # derruba só o túnel (o link continua no ar)
./lab.sh restore <link>                          # volta esse link ao normal
./lab.sh reset                                   # volta todos ao normal (mantém o histórico)
```

`<link>` aceita o nome completo (`fw-escola-c`) ou um trecho único (`escola`); com `vpn` no
nome (`vpn-escola`) vale para o túnel. Os cenários
ficam salvos e são reaplicados se você rodar `./lab.sh up` de novo.

| Cenário | O que o template NOTO - LINK INTERNET mostra |
|---|---|
| `degrade escola latency 150` | `icmppingsec` ~150 ms → **Latência está Alta** (~2 min) |
| `degrade clinica loss 30` | `icmppingloss` oscila (0/33/67/100%) → sem alerta: limite padrão é 80% |
| `saturate clinica` | **Download está acima do limite** (~3 min) |
| `fail industria` | **Sem resposta de Ping** (~2 min). Itens SNMP param de atualizar. A VPN do cliente cai junto |
| `fail vpn-clinica` | VPN-CLINICA-H: **Sem resposta de Ping** (~2 min); o link FW-CLINICA-H segue normal |

> **Sobre perda de pacote com o template padrão:** `icmpping`/`icmppingloss` sem parâmetros
> enviam **3 pacotes** por coleta, então a perda só assume 0, 33, 67 ou 100%. Com
> `{$PERDA_WARN}=80`, perdas altas costumam zerar as 3 respostas e o Zabbix registra
> "sem ping" (o alerta de perda fica suprimido pela dependência). Para alertar perda moderada,
> ajuste `{$PERDA_WARN}` no host (ex.: `20`).

> **Link fora (`fail`):** como o Zabbix monitora o FW pela própria WAN, quando ela cai o SNMP
> também para. Download/upload/status ficam com o último valor antigo; quem acusa a queda é o
> ping. Os dashboards tratam o link como **Offline** quando `icmpping = 0`.

## Dashboard Kit NOC · Links de internet

Arquivo: `kit-noc-links-internet.json` (importado no Grafana). Lê **somente** as chaves do NOTO - LINK INTERNET:
`icmpping`, `icmppingloss`, `icmppingsec`, `net.if.in.linkprincipal`, `net.if.out.linkprincipal`,
`net.if.status`, `net.if.speed`.

| Bloco | De onde vem |
|---|---|
| Faixa de alertas / Onde agir primeiro | link sem ping, acima do contratado, uso ≥ 80%, perda e latência |
| Links operacionais | `icmpping` = 1 na última coleta (sem coleta há mais de 5 min = indisponível) |
| Tráfego agregado / Ocupação | soma de `net.if.in.linkprincipal` / banda contratada |
| Tabela | download, upload, utilização (maior sentido ÷ contratado), perda e latência (média 5 min) |
| Disp. 7d e painel **SLA · últimos 7 dias** | média do `icmpping` hora a hora (`groupBy(1h, avg)`), sem trends |

**Banda contratada do link** (o template não tem esse dado), em ordem de prioridade:
1. `| banda` no **nome visível do host**: `Industria A | 500M` (o nome técnico continua `FW-INDUSTRIA-A`);
2. variável oculta `contratos` do dashboard: `Industria A=500M; FW-ESCOLA-C=200M`;
3. velocidade da porta (`net.if.speed`).

**Variáveis**: `Zabbix` (datasource), `Grupo` (grupo de hosts), `SLA (%)` (meta, padrão 99,5).
Ocultas (Configurações → Variáveis): `util_warn` (80%), `lat_warn` (100 ms, igual ao
`{$LATENCIA_WARN}`), `perda_warn` (5%), `contratos`.

> **Por que 7 dias:** o `icmpping` do NOTO - LINK INTERNET guarda histórico de 7 dias e não
> tem trends. O SLA usa esse histórico; não é preciso mudar o template.

Para editar o visual: altere `dashboards-src/` (HTML/CSS/JS) e rode
`python3 scripts/build_dashboards.py` (gera os JSON e o pacote `Dashboards/`; fica só na máquina do professor).

### Importar em outro Grafana
1. Plugins: `alexanderzobnin-zabbix-app` (habilite o app) e `marcusolsson-dynamictext-panel` (Business Text).
2. Em `grafana.ini`: `[panels] disable_sanitize_html = true` (ou `GF_PANELS_DISABLE_SANITIZE_HTML=true`).
3. *Dashboards → New → Import* → envie o JSON. A variável `Zabbix` escolhe o datasource
   Zabbix existente; selecione o `Grupo` dos seus links.
4. Nos hosts: nome visível com `| banda` (ou preencha a variável `contratos`).

## Dashboards Kit NOC (seus 4 dashboards, adaptados)

Mesmo visual dos originais (cabeçalho com relógio, cards de vidro, sparklines, Top 10, mapa).
**Nada fixo no código**: não existe mais `INFRA_CONFIG`. Tudo vem do Zabbix, então o comprador
só importa o JSON e escolhe o datasource.

| Arquivo | Template | O que mostra |
|---|---|---|
| `kit-noc-links.json` | NOTO - LINK INTERNET | um card por link: down/up, % da banda, 95th, latência, perda |
| `kit-noc-top-consumo.json` | NOTO - LINK INTERNET | cards com busca (nome, operadora, cidade), 6 por página, botão **Diagnóstico do link** + Top 10 por % da banda |
| `kit-noc-geomap.json` | NOTO - LINK INTERNET | cards + mapa (Leaflet + OpenStreetMap, **gratuito e sem chave de API**) |
| `kit-noc-vpn.json` | NOTO - VPN | um card por túnel: SECURE_UP/TUNNEL_DOWN, perda, latência, tipo e criptografia |

**O que o comprador configura em cada host** (tudo opcional, exceto a banda):

| Onde | Exemplo | Usado em |
|---|---|---|
| Nome visível do host | `Industria A \| 500M` ou `Industria A \| 500M/250M` (down/up) | banda contratada (sem isso: velocidade da porta) |
| Macro `{$OPERADORA}` | `Rede Alfa` | etiqueta da operadora, busca |
| Macro `{$LOCAL}` | `Curitiba - PR` | posição no mapa, etiqueta de cidade, busca |
| Macro `{$COORDENADAS}` | `-25.43,-49.27` | posição exata no mapa (vence o `{$LOCAL}`) |
| Macro `{$VPN_TIPO}` / `{$VPN_CRIPTO}` | `IPsec S2S` / `AES-256-GCM / SHA384` | cards de VPN |

* **Separação link × VPN:** os dois templates têm itens com o mesmo nome (Disponibilidade,
  Latência...). Os dashboards filtram pelas **tags dos itens**: `LINK:`/`BANDA:` para links e
  `VPN:` para túneis. Pode deixar tudo no mesmo grupo.
* **Mapa:** o `{$LOCAL}` reconhece todas as capitais e as maiores cidades. Cidade fora da lista:
  use `{$COORDENADAS}`; se só a UF for reconhecida, o ponto vai na capital (marcado "aprox.").
  Hosts sem localização aparecem num aviso no canto do mapa. A variável **Matriz (mapa)** é o
  centro das linhas (padrão `São Paulo - SP`; vazio = sem linhas).
* **Por que o mapa antigo pedia API key:** ele usava tiles de um provedor comercial. Agora usa o
  OpenStreetMap, escurecido via CSS para manter o visual dark. Se o OSM falhar, troca sozinho
  para o CARTO. Para usar um servidor de tiles próprio, preencha a variável oculta `mapa_tiles`.
  O Grafana (o navegador de quem abre) precisa acessar `unpkg.com` e `tile.openstreetmap.org`.
* **Diagnóstico do link** (TOP CONSUMO): o botão aparece em link offline, com uso > 85% ou perda.
  Explica o problema em texto: offline, acima do contratado, perda, latência, banda não informada.
* O dashboard **Kit NOC** também passou a filtrar pelas tags, para não misturar as VPNs.

## Dashboards do Kit Provedor

Entregues à parte (`Dashboards/06` e `Dashboards/07`): o aluno importa no Grafana (*Dashboards → New → Import*)
e escolhe o datasource **Zabbix**. Mesmo visual do Kit NOC.

| Dashboard | Lê | Convenção que o aluno segue no equipamento dele |
|---|---|---|
| **Kit Provedor · Links** (`provedor-links.json`) | NOTO TI - Interfaces SNMP + NOTO TI - BGP | descrição da interface com `\| banda` (`TRANSITO A AS64601 \| 1G`); `AS<número>` marca trânsito/IX e cruza com a sessão BGP; enlace interno descrito nas duas pontas vira uma linha `A ↔ B` |
| **Kit Provedor · OLTs** (`provedor-olt.json`) | só as chaves `olt.*` do padrão NOTO TI de OLT | template da OLT no padrão (VSOL, Huawei, Fiberhome ou clone do NOTO TI - OLT Lab) |

* **Links:** links no ar, tráfego e ocupação do trânsito, sessões BGP, tabela por criticidade
  ("link no ar mas BGP fora" ≠ "interface down"), onde agir primeiro e gráfico de trânsito/IX.
* **OLTs:** ONUs online, clientes sem serviço (LOS, sem energia, offline), sinal RX médio e pior,
  tráfego GPON, PONs com barra de ONUs por estado. Em "onde agir": PON fora, **queda em massa**
  (muitas ONUs em LOS na mesma PON = problema antes do splitter, um técnico resolve todos), LOS
  (enviar técnico), sem energia (não enviar técnico), sinal crítico/baixo, autenticação.
* Variáveis ocultas: `util_warn` (80%) no de Links; `rx_warn` / `rx_crit` (-25 / -27 dBm, iguais às
  macros dos templates) no de OLT.

## Grafana
* Datasource `Zabbix` (uid `zabbix-lab`) provisionado com o usuário somente-leitura `grafana`.
* `GF_PANELS_DISABLE_SANITIZE_HTML=true` é necessário para o Business Text renderizar o HTML/JS
  dos dashboards. Em produção, ative o mesmo (`[panels] disable_sanitize_html = true`).

## Arquivos

| Caminho | Conteúdo |
|---|---|
| `compose.yaml` | serviços e redes |
| `lab.sh`, `scripts/lab.py` | automação do lab |
| `node/` | imagem dos nós (FW, NOC, operadora e equipamentos do provedor) |
| `msp/` | Kit NOC: `links.csv`, `vpns.csv`, `templates/` (NOTO - LINK INTERNET e NOTO - VPN originais) |
| `provedor/` | Kit Provedor: `topologia.json` (equipamentos, enlaces, BGP, OLTs, fluxos) |
| `node/trafego.sh` | gerador de tráfego dos links MSP |
| `node/provedor.py` | configura cada equipamento do provedor (interfaces, SNMP, FRR/BGP, tráfego) |
| `node/olt_sim.py` | OLT simulada (SNMP pass_persist, OIDs fictícios do padrão NOTO TI de OLT) |
| `dashboards-src/` | fonte dos dashboards (HTML/CSS/JS): `common.js`, `noto.css` e a logo valem para todos; `msp/` = Kit NOC (`kitnoc.js` + `sla.js`; `cards/` = LINKS, TOP CONSUMO, GEOMAP e VPN); `provedor/` = Kit Provedor |
| `scripts/build_dashboards.py` | gera os JSON dos dashboards |
| `Templates/` | templates Zabbix do Kit Provedor (OLTs, MikroTik, Huawei, BGP, rádios, servidores) com alertas em cascata; `LEIA-ME.md`, `CORRECOES.md` e o gerador em `_gerador/`. **Fora do git** |
| `Dashboards/` | pacote para os alunos: uma pasta por dashboard (JSON + preview.png + LEIA-ME). Gerado pelo `build_dashboards.py`, **fora do git** |
| `grafana/provisioning/` | plugin Zabbix e datasource (sem dashboards: o aluno importa) |
| `producao/` | instalação de produção: `producao.sh` (up, status, backup, restore, update, destroy), `compose.yaml` e `LEIA-ME.md` |
