# Produção · Zabbix + Grafana prontos (Kit NOC e Kit Provedor)

O laboratório (`./lab.sh`) é para aprender. Esta pasta sobe o **monitoramento de verdade**, no servidor do
provedor ou do cliente, com:

* Zabbix 7.0 LTS + PostgreSQL + Grafana, em containers;
* os templates do(s) kit(s) importados;
* os grupos de hosts criados;
* a macro global da community SNMP;
* o Grafana ligado ao Zabbix, com os dashboards importados.

Nada do laboratório entra aqui: só os equipamentos que **você** cadastrar.

## 1. O servidor

* Ubuntu Server 24.04 (ou outro Linux com Docker), com acesso à rede de gerência dos equipamentos.
* Até ~500 equipamentos: 4 vCPU, 8 GB de RAM e 100 GB de disco SSD. Mais que isso: aumente RAM e disco.
* Docker Engine e Docker Compose instalados (mesmo passo a passo do `README.md` do repositório).

## 2. Instalar

```bash
git clone https://github.com/josezipf/kit-noc-link.git
cd kit-noc-link
# copie para cá as pastas que vieram na compra:
#   Templates/   (Kit Provedor)
#   Dashboards/  (dashboards dos kits)
./producao/producao.sh up --provedor --community SUA_COMMUNITY        # Kit Provedor
./producao/producao.sh up --msp                                       # Kit NOC (MSP)
./producao/producao.sh up --provedor --msp --community SUA_COMMUNITY  # os dois
```

**Ainda não recebeu os pacotes (`Templates/`, `Dashboards/`)?** Pode instalar mesmo assim, inclusive sem escolher
kit: `./producao/producao.sh up`. Ele sobe Zabbix e Grafana prontos e avisa o que ficou faltando. Quando os
pacotes chegarem, descompacte na pasta `kit-noc-link` e rode o `up` de novo (com `--provedor`/`--msp`, se ainda não
escolheu): ele importa os templates e os dashboards sem apagar nada do que você já cadastrou.

No fim, o comando mostra os endereços e as senhas (geradas na hora, só desta instalação):

```
Zabbix : http://IP-DO-SERVIDOR:8080   usuário Admin
Grafana: http://IP-DO-SERVIDOR:3000   usuário admin
```

As senhas ficam em `producao/.env`. **Guarde esse arquivo**: sem ele, você não entra no sistema.

Pode rodar o `up` de novo quando quiser, por exemplo para incluir o outro kit ou depois de atualizar os templates.
Ele não apaga nada. Os dashboards que já existem não são substituídos, então as suas alterações ficam.

## 3. Cadastrar os equipamentos reais

No Zabbix: *Coleta de dados → Hosts → Criar host*. Preencha:

* **Nome**: o nome do equipamento (aparece nos alertas e dashboards).
* **Grupo**: um dos grupos criados (`Provedor/Rede`, `Provedor/OLTs`, `Provedor/Servidores`,
  `MSP/Links de internet`, `MSP/VPNs`) ou um seu.
* **Interface**: SNMP com o IP de gerência (agente, no caso de servidor Linux).
* **Templates**:
  * Kit Provedor: a tabela *O que vincular em cada host* do `Templates/LEIA-ME.md`.
    Exemplo: roteador de borda = `NOTO TI - SNMP` + `NOTO TI - Interfaces SNMP` + `NOTO TI - BGP`.
  * Kit NOC: `NOTO - LINK INTERNET` ou `NOTO - VPN`, com as macros do link (velocidade contratada, operadora,
    cidade), como no laboratório.

Nos equipamentos, use as **mesmas convenções do laboratório**. É isso que faz os dashboards se montarem sozinhos:

* **Descrição da interface** de trânsito/IX: `TRANSITO A AS64601 | 1G` (nome, AS e banda contratada).
* **Descrição da ONU**: nome do cliente.
* **OLT de fabricante sem template pronto**: clone o `NOTO TI - OLT Lab` e troque só os OIDs
  (`Templates/05 - OLTs (padrão NOTO TI)/PADRAO-OLT.md`).

**Primeiro equipamento de cada modelo:** confira em *Monitoramento → Dados recentes* se os itens têm valor.
Os OIDs de fabricante vêm dos templates originais. Se algum item ficar "sem suporte", ajuste o OID no template.

## 4. Dia a dia

| Comando | O que faz |
|---|---|
| `./producao/producao.sh status` | containers, hosts, alertas, Grafana e último backup |
| `./producao/producao.sh creds` | endereços e senhas |
| `./producao/producao.sh backup` | backup do Zabbix e do Grafana em `producao/backups/` |
| `./producao/producao.sh update` | faz backup e atualiza (sempre dentro do Zabbix 7.0 LTS) |
| `./producao/producao.sh logs zabbix-server` | logs de um serviço (`db`, `zabbix-server`, `zabbix-web`, `grafana`) |
| `./producao/producao.sh down` | para tudo (os dados ficam; `up` volta) |
| `./producao/producao.sh restore <arquivo>` | volta um backup. **Substitui tudo: pede confirmação** |
| `./producao/producao.sh destroy` | apaga a instalação inteira. **Pede confirmação** |

**Backup:**
* Faça pelo menos 1 por semana. Pode agendar no cron:
  `0 3 * * 0 cd /caminho/kit-noc-link && ./producao/producao.sh backup`.
* Copie o arquivo para **fora do servidor**. Ele contém as senhas, então guarde com cuidado.

**Restore e destroy** apagam dados que não têm volta. Os dois avisam e só continuam se você digitar `APAGAR`.

## 5. Bom saber

* **Portas:** 8080 (Zabbix), 3000 (Grafana) e 10051 (agentes ativos e proxies). Se a 8080 ou a 3000 estiver ocupada na
  primeira instalação, o `up` usa a próxima livre e mostra qual.
* **Firewall:** libere essas portas só para a rede do NOC. Para acessar de fora, use VPN.
* **Rede interna dos containers:** `198.18.250.0/24`. Se colidir com alguma rede sua, apague a instalação ainda
  vazia e rode `PROD_REDE=10.99.99.0/24 ./producao/producao.sh up ...` (escolha uma faixa livre).
* **O Grafana lê o Zabbix com o usuário `grafana`.** Ele só pode consultar (nada de alterar) e enxerga todos os
  grupos, inclusive os que você criar depois. Ele não entra na tela do Zabbix.
* **Host "Zabbix server":**
  * Sai o template "Linux by Zabbix agent": o container não tem agente, e ele só daria alerta falso.
  * Com o Kit Provedor, a saúde do Zabbix passa a ser medida pelo `NOTO TI - Saude do Zabbix server`.
  * Alguns itens ficam "sem suporte" por padrão (VMware, Java, IPMI, relatórios). É normal: esses processos vêm desligados.
* **Novos dashboards:** crie no Grafana normalmente. Os do kit são um ponto de partida.
