#!/usr/bin/env python3
"""Kit Provedor: configura um equipamento da topologia (provedor/topologia.json) dentro do container.

  - renomeia as interfaces (pelo IP) e grava a descrição (ifAlias) e a velocidade no SNMP;
  - sobe o FRR com BGP de verdade (borda e upstreams) e publica a BGP4-MIB no SNMP via AgentX;
  - OLTs: liga o simulador (OIDs fictícios, número de empresa 32473) no SNMP;
  - gera o tráfego de cada enlace com iperf3 (pico às 21h do "dia" do lab);
  - termina rodando o snmpd em primeiro plano (se ele cair, o container reinicia).
"""
import json
import math
import os
import random
import subprocess
import threading
import time
from pathlib import Path

P = os.environ["LAB_NET_PREFIX"]
EU = os.environ["LAB_EQUIP"]
DIA = int(os.environ.get("LAB_DIA_SEGUNDOS", "3600"))
TOPO = json.loads(Path("/lab/provedor/topologia.json").read_text(encoding="utf-8"))
DEV = TOPO["equipamentos"][EU]
ip = lambda sufixo: f"{P}.{sufixo}"


def sh(*cmd, check=True):
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def if_por_ip(endereco):
    for linha in sh("ip", "-o", "-4", "addr", "show").stdout.splitlines():
        partes = linha.split()
        if partes[3].split("/")[0] == endereco:
            return partes[1]
    raise SystemExit(f"{EU}: interface com IP {endereco} não encontrada")


def renomear(endereco, nome):
    atual = if_por_ip(endereco)
    if atual != nome:
        sh("ip", "link", "set", atual, "down")
        sh("ip", "link", "set", atual, "name", nome)
    sh("ip", "link", "set", nome, "up")


def curva(t=None):
    """Tráfego residencial: ~25% de madrugada, pico às 21h."""
    h = ((t or time.time()) % DIA) / DIA * 24
    return 0.25 + 0.75 * ((1 + math.cos(2 * math.pi * (h - 21) / 24)) / 2) ** 1.6


# ---------------------------------------------------------------- interfaces e SNMP
snmpd = [
    "agentAddress udp:161",
    f"rocommunity {os.environ['LAB_SNMP_COMMUNITY']} default",
    f"sysLocation Provedor Exemplo - {DEV.get('nome_visivel', EU)}",
    "sysContact noc@provedor-exemplo.invalid",
    "sysServices 78",
    "pass .1.3.6.1.2.1.31.1.1.1.18 /usr/local/bin/ifalias-pass",
]
if DEV.get("mgmt"):
    renomear(ip(DEV["mgmt"]), "mgmt")
    sh("ip", "link", "set", "mgmt", "alias", "GERENCIA")
for itf in DEV["interfaces"]:
    renomear(ip(itf["ip"].split("/")[0]), itf["nome"])
    sh("ip", "link", "set", itf["nome"], "alias", itf["alias"])
    snmpd.append(f"interface {itf['nome']} 6 {itf['velocidade'] * 1_000_000}")

if "bgp" in DEV:
    snmpd += ["master agentx", "agentXSocket tcp:127.0.0.1:705"]
if "olt" in DEV:
    snmpd.append("pass_persist .1.3.6.1.4.1.32473.1 /usr/bin/python3 /usr/local/bin/lab-olt-sim")
Path("/tmp/net-snmp").mkdir(exist_ok=True)
Path("/tmp/snmpd.conf").write_text("\n".join(snmpd) + "\n")


# ---------------------------------------------------------------- BGP (FRR)
def iniciar_frr():
    bgp = DEV["bgp"]
    rid = ip(DEV["mgmt"]) if DEV.get("mgmt") else ip(DEV["interfaces"][0]["ip"].split("/")[0])
    linhas = ["frr defaults traditional", f"hostname {EU}", "log stdout warnings"]
    if DEV.get("mgmt"):
        linhas.append("agentx")
    linhas += [f"router bgp {bgp['asn']}", f" bgp router-id {rid}", " no bgp ebgp-requires-policy",
               " no bgp network import-check", " timers bgp 3 9"]
    for v in bgp["vizinhos"]:
        linhas += [f" neighbor {ip(v['ip'])} remote-as {v['asn']}", f" neighbor {ip(v['ip'])} description {v['nome']}"]
    linhas.append(" address-family ipv4 unicast")
    if "anuncia" in bgp:
        a, b, c, _ = (int(x) for x in bgp["anuncia"]["base"].split("."))
        base = (a << 16) + (b << 8) + c
        for i in range(bgp["anuncia"]["quantidade"]):
            n = base + i
            linhas.append(f"  network {n >> 16}.{(n >> 8) & 255}.{n & 255}.0/24")
    else:
        linhas.append(f"  network {TOPO['prefixo_proprio']}")
    linhas += [" exit-address-family", "line vty", ""]
    frr = Path("/tmp/frr")
    frr.mkdir(exist_ok=True)
    (frr / "bgpd.conf").write_text("\n".join(linhas))
    (frr / "zebra.conf").write_text(f"hostname {EU}\nlog stdout warnings\n")
    (frr / "vtysh.conf").write_text("")
    snmpconf = Path("/tmp/snmpfrr")
    snmpconf.mkdir(exist_ok=True)
    (snmpconf / "frr.conf").write_text("agentXSocket tcp:127.0.0.1:705\nagentXPingInterval 5\n")
    comum = ["-u", "root", "-g", "frrvty", "--vty_socket", "/tmp/frr", "-z", "/tmp/frr/zserv.api", "-d"]
    # daemons com -d: a saída vai para /dev/null (se ficasse num pipe, o processo-filho o seguraria aberto)
    quieto = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": True}
    subprocess.run(["/usr/lib/frr/zebra", "-f", "/tmp/frr/zebra.conf", "-i", "/tmp/frr/zebra.pid", *comum], **quieto)
    extra = ["-M", "snmp"] if DEV.get("mgmt") else []
    env = dict(os.environ, SNMPCONFPATH="/tmp/snmpfrr")
    subprocess.run(["/usr/lib/frr/bgpd", "-f", "/tmp/frr/bgpd.conf", "-i", "/tmp/frr/bgpd.pid", *comum, *extra], env=env, **quieto)


if "bgp" in DEV:
    iniciar_frr()


# ---------------------------------------------------------------- tráfego (iperf3)
def servidor(porta):
    while True:
        subprocess.run(["iperf3", "-s", "-p", str(porta), "--idle-timeout", "75"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)


def taxa(fluxo, sentido):
    cen = Path(f"/tmp/cenario-{fluxo['id']}")
    cenario = cen.read_text().strip() if cen.exists() else "normal"
    if cenario == "saturar":
        mbps = fluxo["contratado"] * random.uniform(1.09, 1.15) * (1 if sentido == "down" else 0.15)
        return max(1, int(mbps * 1000))
    if fluxo.get("da_olt"):  # a OLT simulada informa a soma do tráfego das ONUs: o uplink acompanha
        arq = Path("/tmp/olt-taxa")
        if arq.exists() and time.time() - arq.stat().st_mtime < 300:
            down, up = (float(x) for x in arq.read_text().split())
            return max(1, int((down if sentido == "down" else up) / 1000))
    pico = fluxo["pico_down" if sentido == "down" else "pico_up"]
    return max(1, int(pico * curva() * random.uniform(0.94, 1.06) * 1000))


def cliente(fluxo, sentido):
    porta = fluxo["porta"] + (0 if sentido == "down" else 1)
    while True:
        cmd = ["iperf3", "-c", ip(fluxo["ip_servidor"]), "-p", str(porta), "-u", "-b", f"{taxa(fluxo, sentido)}K",
               "-l", "8000", "-t", "60"] + (["-R"] if sentido == "down" else [])
        inicio = time.time()
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if time.time() - inicio < 3:
            time.sleep(2)


for fluxo in TOPO["fluxos"]:
    if fluxo["servidor"] == EU:
        for p in (fluxo["porta"], fluxo["porta"] + 1):
            threading.Thread(target=servidor, args=(p,), daemon=True).start()
    if fluxo["cliente"] == EU:
        for s in ("down", "up"):
            threading.Thread(target=cliente, args=(fluxo, s), daemon=True).start()

# ---------------------------------------------------------------- SNMP em primeiro plano
Path("/tmp/lab-node.ready").touch()
raise SystemExit(subprocess.run(["snmpd", "-f", "-C", "-c", "/tmp/snmpd.conf", "-LE3"]).returncode)
