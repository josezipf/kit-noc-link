#!/usr/bin/env python3
"""Laboratório NOTO TI - Dashboards de links (Kit NOC / MSP e Kit Provedor).

Gera dados de monitoramento realistas para construir e validar os dashboards Grafana.
  Kit NOC (msp)      : 4 links de internet de clientes MSP, template NOTO - LINK INTERNET.
  Kit Provedor       : topologia de provedor (borda com BGP real, BNG, agregação, 2 OLTs simuladas, servidores)
                       monitorada com os templates NOTO da pasta Templates/ (provedor/topologia.json).
Escolha os kits na instalação: ./lab.sh up --msp | --provedor (sem flag = os dois).
"""
import argparse
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / ".lab-state.json"
PROJECT = "nototi-dashboards-links"
KITS = ("msp", "provedor")

GROUP_REDE = "NOTO LAB/Provedor - Rede"
GROUP_OLT = "NOTO LAB/Provedor - OLTs"
GROUP_SRV = "NOTO LAB/Provedor - Servidores"
GROUP_MSP = "NOTO LAB/MSP - Links de internet"
GROUP_VPN = "NOTO LAB/MSP - VPNs"
GRAFANA_ZBX_USER = "grafana"
GRAFANA_ZBX_GROUP = "NOTO LAB - Grafana (somente leitura)"
TPL_LINK = "NOTO - LINK INTERNET"         # template do professor: importado sem nenhuma alteração
TPL_VPN = "NOTO - VPN"                   # mesmo arquivo do NOTO - LINK INTERNET, também sem alteração
TEMPLATE_FILES = {"msp": "msp/templates/template_internet_vpn.yaml"}
TEMPLATES_PROVEDOR = ROOT / "Templates" / "TODOS - Kit Provedor.yaml"   # pasta local do kit (fora do git)
TPL_PROV = {"snmp": "NOTO TI - SNMP", "disp": "NOTO TI - Disponibilidade", "ifs": "NOTO TI - Interfaces SNMP", "bgp": "NOTO TI - BGP",
            "olt": "NOTO TI - OLT Lab", "linux": "NOTO TI - Linux agente", "zabbix": "NOTO TI - Saude do Zabbix server"}
TEMPLATES_POR_PAPEL = {"borda": ["snmp", "ifs", "bgp"], "concentrador": ["snmp", "ifs"], "switch": ["snmp", "ifs"],
                       "olt": ["snmp", "olt", "ifs"]}
TPL_LINK_MD5 = "75a6c0f0af7cd15530cf340ddd9ba304"


class LabError(RuntimeError):
    pass


def say(message=""):
    print(message, flush=True)


def read_csv(path):
    rows = []
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            rows.append([part.strip() for part in line.split(";")])
    return rows


def topologia():
    return json.loads((ROOT / "provedor" / "topologia.json").read_text(encoding="utf-8"))


def prov_equipamentos(somente_monitorados=False):
    topo = topologia()
    return [dict(d, host=nome) for nome, d in topo["equipamentos"].items() if d.get("monitorar") or not somente_monitorados]


def prov_servidores():
    return [dict(d, host=nome) for nome, d in topologia()["servidores"].items()]


def prov_enlaces():
    """Enlaces com tráfego (alvos de saturate/fail/degrade): a interface do lado 'cliente' de cada fluxo."""
    topo = topologia()
    out = []
    for f in topo["fluxos"]:
        cli = topo["equipamentos"][f["cliente"]]
        rede = ipaddress.ip_network("10.0." + f["ip_servidor"] + "/29", strict=False)
        itf = next(i for i in cli["interfaces"] if ipaddress.ip_address("10.0." + i["ip"].split("/")[0]) in rede)
        out.append({"kind": "enlace", "service": f["id"], "host": f"{f['cliente']} {itf['nome']}", "nome": itf["alias"],
                    "container": cli["servico"], "ifname": itf["nome"], "contratado": f["contratado"], "fluxo": f,
                    "servidor": topo["equipamentos"][f["servidor"]]["servico"]})
    return out


def msp_links():
    return [{"n": n, "host": host, "suffix": int(suffix), "descricao": desc, "nome": desc.split(" - ")[0].strip(),
             "contratado": int(contratado), "perfil": perfil, "operadora": operadora, "local": local, "kind": "fw",
             "service": host.lower()}
            for n, (host, suffix, desc, contratado, perfil, operadora, local) in enumerate(read_csv("msp/links.csv"), 1)]


def msp_vpns():
    links = {link["host"]: link for link in msp_links()}
    return [{"host": host, "fw": links[fw], "ip": lan, "nome": nome, "tipo": tipo, "cripto": cripto, "kind": "vpn",
             "service": host.lower()}
            for host, fw, lan, nome, tipo, cripto in read_csv("msp/vpns.csv")]


def kit_services(kit):
    if kit == "msp":
        return ["operadora"] + [link["service"] for link in msp_links()]
    return [d["servico"] for d in prov_equipamentos()] + [d["servico"] for d in prov_servidores()]


# ---------------------------------------------------------------- docker / env

def run(args, capture=False, check=True, timeout=None):
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=capture, timeout=timeout)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise LabError(f"Falhou: {' '.join(args[:8])}. {detail}")
    return result


def active_kits(env=None):
    env = env or (read_env() if ENV_PATH.exists() else {})
    kits = [kit for kit in env.get("LAB_KITS", ",".join(KITS)).split(",") if kit in KITS]
    return kits or list(KITS)


def compose(*args, capture=False, check=True, timeout=None, kits=None):
    profiles = []
    for kit in (kits if kits is not None else active_kits()):
        profiles += ["--profile", kit]
    return run(["docker", "compose", "--env-file", str(ENV_PATH), "-p", PROJECT,
                "-f", str(ROOT / "compose.yaml"), *profiles, *args], capture=capture, check=check, timeout=timeout)


def dexec(service, *cmd, check=False, timeout=20):
    return compose("exec", "-T", service, *cmd, capture=True, check=check, timeout=timeout, kits=KITS)


def require_docker():
    if not shutil.which("docker"):
        raise LabError("Docker não encontrado. Instale o Docker Engine e o plugin Docker Compose.")
    if run(["docker", "info"], capture=True, check=False).returncode:
        raise LabError("Sem acesso ao Docker. Inicie o serviço e use um usuário do grupo docker.")
    if run(["docker", "compose", "version"], capture=True, check=False).returncode:
        raise LabError("O plugin Docker Compose não está instalado (comando docker compose).")


def read_env():
    if not ENV_PATH.exists():
        raise LabError("Laboratório ainda não configurado. Execute ./lab.sh up.")
    return dict(line.split("=", 1) for line in ENV_PATH.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def set_env(key, value):
    lines = ENV_PATH.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def free_port(preferred, exclude=()):
    for port in range(preferred, preferred + 200):
        if port in exclude:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise LabError("Não encontrei uma porta local livre.")


def lab_networks(prefix):
    return [ipaddress.ip_network(f"{prefix}.{part}.0/24") for part in (0, 1, 2, 3, 4)]


def occupied_networks():
    occupied = []
    ids = run(["docker", "network", "ls", "-q"], capture=True).stdout.split()
    if ids:
        for network in json.loads(run(["docker", "network", "inspect", *ids], capture=True).stdout):
            for conf in (network.get("IPAM") or {}).get("Config") or []:
                if conf.get("Subnet"):
                    occupied.append(ipaddress.ip_network(conf["Subnet"], strict=False))
    if shutil.which("ip"):
        result = run(["ip", "-j", "-4", "route", "show", "table", "all"], capture=True, check=False)
        if result.returncode == 0:
            for route in json.loads(result.stdout or "[]"):
                dst = route.get("dst", "default")
                if dst != "default" and re.fullmatch(r"[\d.]+(/\d+)?", dst):
                    occupied.append(ipaddress.ip_network(dst, strict=False))
    return [net for net in occupied if net.version == 4]


def choose_prefix(requested=None):
    occupied = occupied_networks()
    candidates = [requested] if requested else [f"10.{x}" for x in range(150, 200)] + [f"172.{x}" for x in range(24, 31)]
    for prefix in candidates:
        if not re.fullmatch(r"\d{1,3}\.\d{1,3}", prefix or ""):
            raise LabError("LAB_NET_PREFIX deve ter dois octetos, por exemplo 10.150.")
        networks = lab_networks(prefix)
        if not all(net.is_private for net in networks):
            raise LabError("Use endereçamento IPv4 privado (10.x, 172.16-31.x, 192.168).")
        if not any(net.overlaps(other) for net in networks for other in occupied):
            return prefix
    raise LabError("Todas as faixas candidatas conflitam com redes existentes. Defina LAB_NET_PREFIX=a.b livre e rode de novo.")


def initialize():
    if ENV_PATH.exists():
        env = read_env()
        if "LAB_DIA_SEGUNDOS" not in env:
            set_env("LAB_DIA_SEGUNDOS", "3600")
        return read_env()
    leftovers = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"], capture=True).stdout.strip()
    if leftovers:
        raise LabError(f"Existe um projeto {PROJECT} rodando sem o .env desta pasta. Remova-o com docker compose -p {PROJECT} down -v.")
    prefix = choose_prefix(os.environ.get("LAB_NET_PREFIX"))
    published = run(["docker", "ps", "--format", "{{.Ports}}"], capture=True).stdout
    reserved = {int(port) for port in re.findall(r":(\d+)->", published)}
    zbx_port = free_port(8280, reserved)
    graf_port = free_port(3300, reserved | {zbx_port})
    env = {
        "LAB_NET_PREFIX": prefix,
        "LAB_BIND": os.environ.get("LAB_BIND", "127.0.0.1"),
        "LAB_ZBX_PORT": str(zbx_port),
        "LAB_GRAFANA_PORT": str(graf_port),
        "LAB_ZABBIX_TAG": "alpine-7.0-latest",
        "LAB_GRAFANA_TAG": "12.3.0",
        "LAB_DIA_SEGUNDOS": "3600",
        "LAB_DB_PASSWORD": secrets.token_hex(20),
        "LAB_ADMIN_PASSWORD": "Noto7!" + secrets.token_urlsafe(12),
        "LAB_GRAFANA_PASSWORD": "Noto7!" + secrets.token_urlsafe(12),
        "LAB_ZBX_GRAFANA_PASSWORD": secrets.token_urlsafe(24),
        "LAB_SNMP_COMMUNITY": "noto" + secrets.token_hex(6),
    }
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("# Gerado pelo ./lab.sh up. Preserve este arquivo (senhas desta instalação).\n")
        stream.write("\n".join(f"{key}={value}" for key, value in env.items()) + "\n")
    say(f"Configuração criada: redes {prefix}.0-4.x, Zabbix na porta {zbx_port}, Grafana na porta {graf_port}.")
    return env


def ip(env, suffix):
    return f"{env['LAB_NET_PREFIX']}.{suffix}"


def fw_ip(env, link):
    return ip(env, f"4.{link['suffix']}")


# ---------------------------------------------------------------- estado

def read_state():
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}


def save_state(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(STATE_PATH)


# ---------------------------------------------------------------- Zabbix API

class Api:
    def __init__(self, env):
        self.url = f"http://127.0.0.1:{env['LAB_ZBX_PORT']}/api_jsonrpc.php"
        self.env = env
        self.token = None
        self.number = 0

    def call(self, method, params=None, authenticated=True):
        self.number += 1
        headers = {"Content-Type": "application/json-rpc"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps({"jsonrpc": "2.0", "id": self.number, "method": method,
                           "params": {} if params is None else params}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, body, headers), timeout=60) as response:
                result = json.load(response)
        except (OSError, ValueError) as error:
            raise LabError(f"API indisponível em {self.url}: {type(error).__name__}") from error
        if "error" in result:
            error = result["error"]
            raise LabError(f"API {method}: {error.get('message', '')} {error.get('data', '')}")
        return result["result"]

    def login(self, password=None):
        self.token = self.call("user.login", {"username": "Admin", "password": password or self.env["LAB_ADMIN_PASSWORD"]},
                               authenticated=False)
        return self

    def one(self, method, flt, field):
        found = self.call(method, {"output": [field], "filter": flt})
        return found[0][field] if found else None


def wait_for_api(env):
    api = Api(env)
    deadline = time.monotonic() + 600
    last = 0
    while True:
        try:
            version = api.call("apiinfo.version", authenticated=False)
            break
        except LabError:
            if time.monotonic() > deadline:
                raise LabError("O Zabbix não respondeu em 10 minutos. Veja ./lab.sh logs zabbix-server.")
            if time.monotonic() - last > 15:
                say("  aguardando o Zabbix criar o banco e subir a API...")
                last = time.monotonic()
            time.sleep(3)
    if not version.startswith("7.0."):
        raise LabError(f"Este laboratório é para Zabbix 7.0 LTS; encontrado {version}.")
    try:
        api.login()
    except LabError:
        api.login("zabbix")
        admin = api.call("user.get", {"output": ["userid"], "filter": {"username": ["Admin"]}})[0]["userid"]
        api.call("user.update", {"userid": admin, "current_passwd": "zabbix", "passwd": env["LAB_ADMIN_PASSWORD"]})
        api.login()
    return api, version


def import_templates(api, kits):
    if "msp" in kits:
        link_file = ROOT / TEMPLATE_FILES["msp"]
        if hashlib.md5(link_file.read_bytes()).hexdigest() != TPL_LINK_MD5:
            raise LabError(f"{link_file.name} foi alterado. O lab usa o template NOTO - LINK INTERNET original, sem mudanças.")
    rules = {"template_groups": {"createMissing": True, "updateExisting": True},
             "templates": {"createMissing": True, "updateExisting": True},
             "items": {"createMissing": True, "updateExisting": True, "deleteMissing": True},
             "triggers": {"createMissing": True, "updateExisting": True, "deleteMissing": True},
             "valueMaps": {"createMissing": True, "updateExisting": True},
             "templateLinkage": {"createMissing": True}}
    if "msp" in kits:
        api.call("configuration.import", {"format": "yaml", "rules": rules,
                                          "source": (ROOT / TEMPLATE_FILES["msp"]).read_text(encoding="utf-8")})
    if "provedor" in kits:
        if not TEMPLATES_PROVEDOR.exists():
            raise LabError(f"Não achei {TEMPLATES_PROVEDOR.relative_to(ROOT)}. O Kit Provedor usa os templates NOTO da pasta Templates/.")
        renomeia_por_uuid(api, TEMPLATES_PROVEDOR.read_text(encoding="utf-8"))
        regras = dict(rules, discoveryRules={"createMissing": True, "updateExisting": True, "deleteMissing": True},
                      templateDashboards={"createMissing": True, "updateExisting": True},
                      graphs={"createMissing": True, "updateExisting": True, "deleteMissing": True},
                      templateLinkage={"createMissing": True, "deleteMissing": True})
        api.call("configuration.import", {"format": "yaml", "rules": regras, "source": TEMPLATES_PROVEDOR.read_text(encoding="utf-8")})
    wanted = ([TPL_LINK, TPL_VPN] if "msp" in kits else []) + (list(TPL_PROV.values()) if "provedor" in kits else [])
    ids = {}
    for name in wanted:
        ids[name] = api.one("template.get", {"host": [name]}, "templateid")
        if not ids[name]:
            raise LabError(f"Template {name} não encontrado no Zabbix.")
    return ids


def renomeia_por_uuid(api, yaml_texto):
    """Template que já existe com outro nome (mesmo UUID, ex.: "NOTO - SNMP" -> "NOTO TI - SNMP") é renomeado
    antes do import: o Zabbix valida as dependências da cascata pelo nome do template, e o import falharia."""
    novos = re.findall(r"^  - uuid: '?([0-9a-f]{32})'?\n    template: (.+)\n    name: (.+)$", yaml_texto, re.M)
    if not novos:
        return
    atuais = {t["uuid"]: t for t in api.call("template.get", {"output": ["templateid", "uuid", "host", "name"]})}
    for uuid, tecnico, visivel in novos:
        t = atuais.get(uuid)
        tecnico, visivel = tecnico.strip("'\""), visivel.strip("'\"")
        if t and (t["host"], t["name"]) != (tecnico, visivel):
            api.call("template.update", {"templateid": t["templateid"], "host": tecnico, "name": visivel})
            say(f"  template renomeado: {t['name']} -> {visivel}")


def ensure_hostgroup(api, name):
    return api.one("hostgroup.get", {"name": [name]}, "groupid") or api.call("hostgroup.create", {"name": name})["groupids"][0]


def ensure_host(api, host, name, groupid, interface, macros=(), tags=(), description="", templateid=None):
    """Cria ou atualiza o host (idempotente) e garante que está ativo. templateid: um id ou uma lista."""
    hostid = api.one("host.get", {"host": [host]}, "hostid")
    params = {"host": host, "name": name, "groups": [{"groupid": groupid}], "description": description,
              "tags": list(tags), "macros": list(macros), "status": 0}
    if templateid:
        ids = templateid if isinstance(templateid, list) else [templateid]
        params["templates"] = [{"templateid": t} for t in ids]
    if hostid:
        api.call("host.update", {"hostid": hostid, **params})
        current = api.call("hostinterface.get", {"output": ["interfaceid"], "hostids": [hostid], "filter": {"main": 1, "type": interface["type"]}})
        if current:
            api.call("hostinterface.update", {"interfaceid": current[0]["interfaceid"], **{k: v for k, v in interface.items() if k != "type"}})
        return hostid
    return api.call("host.create", {**params, "interfaces": [{**interface, "main": 1, "useip": 1, "dns": ""}]})["hostids"][0]


def disable_hosts(api, names):
    found = api.call("host.get", {"output": ["hostid"], "filter": {"host": names, "status": 0}})
    for host in found:
        api.call("host.update", {"hostid": host["hostid"], "status": 1})
    return len(found)


def snmp_interface(address):
    return {"type": 2, "ip": address, "port": "161",
            "details": {"version": 2, "bulk": 1, "community": "{$SNMP_COMMUNITY}", "max_repetitions": 10}}


def ensure_grafana_user(api, env, groupids):
    """Usuário de leitura que o datasource Zabbix do Grafana usa."""
    rights = [{"id": gid, "permission": 2} for gid in groupids]
    ugid = api.one("usergroup.get", {"name": [GRAFANA_ZBX_GROUP]}, "usrgrpid")
    if ugid:
        api.call("usergroup.update", {"usrgrpid": ugid, "hostgroup_rights": rights})
    else:
        ugid = api.call("usergroup.create", {"name": GRAFANA_ZBX_GROUP, "gui_access": 3, "hostgroup_rights": rights})["usrgrpids"][0]
    userid = api.one("user.get", {"username": [GRAFANA_ZBX_USER]}, "userid")
    params = {"username": GRAFANA_ZBX_USER, "name": "Grafana", "surname": "Laboratorio", "roleid": "1",
              "usrgrps": [{"usrgrpid": ugid}], "passwd": env["LAB_ZBX_GRAFANA_PASSWORD"]}
    if userid:
        api.call("user.update", {"userid": userid, **params})
    else:
        api.call("user.create", params)


def mbps_macro(mbps):
    return f"{mbps // 1000}G" if mbps >= 1000 and mbps % 1000 == 0 else f"{mbps}M"


def provision(api, env, kits):
    community = [{"macro": "{$SNMP_COMMUNITY}", "value": env["LAB_SNMP_COMMUNITY"], "description": "Community SNMP v2c do laboratório"}]
    tpl = import_templates(api, kits)
    groups = {name: ensure_hostgroup(api, name) for name in (GROUP_REDE, GROUP_OLT, GROUP_SRV, GROUP_MSP, GROUP_VPN)}
    hosts = {}
    if "provedor" in kits:
        hosts.update(provision_provedor(api, env, tpl, groups, community))
    else:
        n = disable_hosts(api, [d["host"] for d in prov_equipamentos(True)] + [d["host"] for d in prov_servidores()])
        if n:
            say(f"  Kit Provedor fora desta instalação: {n} hosts desativados no Zabbix (histórico preservado).")
    if "msp" in kits:
        for link in msp_links():
            index = wan_index(env, link)
            if index is None:
                raise LabError(f"{link['host']}: não achei a interface wan1 via SNMP para ajustar o {{$INDEX}}.")
            # valor exato em bps: nas expressões do Zabbix o sufixo M vale 1024*1024 (400M = 419 Mbps)
            warn = str(link["contratado"] * 8 // 10 * 1_000_000)
            macros = community + [
                {"macro": "{$INDEX}", "value": str(index), "description": "ifIndex da wan1 (descoberto com snmpwalk IF-MIB::ifName)"},
                {"macro": "{$INTERFACE}", "value": "wan1", "description": "Nome da interface WAN"},
                {"macro": "{$BANDA_WARN_DOWN}", "value": warn, "description": f"80% de {link['contratado']} Mbps contratados"},
                {"macro": "{$BANDA_WARN_UP}", "value": warn, "description": f"80% de {link['contratado']} Mbps contratados"},
                # opcionais, só para os dashboards (não são do template): operadora e cidade (mapa)
                {"macro": "{$OPERADORA}", "value": link["operadora"], "description": "Operadora do link (dashboards NOTO)"},
                {"macro": "{$LOCAL}", "value": link["local"], "description": "Cidade - UF (mapa dos dashboards NOTO)"},
            ]
            # nome visível no padrão "Cliente | banda": é de onde o dashboard tira a banda contratada
            visible = f"{link['descricao'].split(' - ')[0].strip()} | {mbps_macro(link['contratado'])}"
            hosts[link["host"]] = ensure_host(api, link["host"], visible, groups[GROUP_MSP], snmp_interface(fw_ip(env, link)), macros,
                                              [{"tag": "kit", "value": "noc"}, {"tag": "contratado", "value": f"{link['contratado']}M"}],
                                              link["descricao"], tpl[TPL_LINK])
        for vpn in msp_vpns():
            # NOTO - VPN como veio: só ping (Download/Upload do template vêm desativados)
            macros = community + [
                {"macro": "{$VPN_TIPO}", "value": vpn["tipo"], "description": "Tipo do túnel (dashboard NOTO - VPN)"},
                {"macro": "{$VPN_CRIPTO}", "value": vpn["cripto"], "description": "Criptografia (dashboard NOTO - VPN)"},
            ]
            hosts[vpn["host"]] = ensure_host(api, vpn["host"], vpn["nome"], groups[GROUP_VPN], snmp_interface(vpn["ip"]), macros,
                                             [{"tag": "kit", "value": "noc"}, {"tag": "link", "value": vpn["fw"]["host"]}],
                                             f"Túnel até a LAN {vpn['ip']} atrás do {vpn['fw']['host']}", tpl[TPL_VPN])
    else:
        n = disable_hosts(api, [link["host"] for link in msp_links()] + [vpn["host"] for vpn in msp_vpns()])
        if n:
            say(f"  Kit NOC fora desta instalação: {n} hosts desativados no Zabbix (histórico preservado).")
    ensure_grafana_user(api, env, list(groups.values()))
    return hosts


def provision_provedor(api, env, tpl, groups, community):
    """Kit Provedor: hosts da topologia com os templates NOTO (base + equipamento, como na produção)."""
    hosts = {}
    for d in prov_equipamentos(True):
        papel = d["papel"]
        ids = [tpl[TPL_PROV[k]] for k in TEMPLATES_POR_PAPEL[papel]]
        macros = community + [{"macro": "{$FILTRO_REDE}", "value": "^ge-",
                               "description": "Interfaces monitoradas pelo NOTO TI - Interfaces SNMP (as de enlace começam com ge-)"}]
        grupo = groups[GROUP_OLT] if papel == "olt" else groups[GROUP_REDE]
        hid = ensure_host(api, d["host"], d.get("nome_visivel", d["host"]), grupo, snmp_interface(ip(env, d["mgmt"])), macros,
                          [{"tag": "kit", "value": "provedor"}, {"tag": "funcao", "value": papel}],
                          f"Provedor Exemplo (fictício): {d.get('nome_visivel', d['host'])}.", ids)
        hosts[d["host"]] = hid
        if papel == "borda":  # OID proprietário Huawei: no lab (roteador Linux/FRR) fica desativado no host
            for it in api.call("item.get", {"hostids": [hid], "output": ["itemid"], "filter": {"key_": "hwIPv4RibRoutes"}}):
                api.call("item.update", {"itemid": it["itemid"], "status": 1})
    for d in prov_servidores():
        hid = ensure_host(api, d["host"], d["nome_visivel"], groups[GROUP_SRV], {"type": 1, "ip": ip(env, d["mgmt"]), "port": "10050"},
                          [], [{"tag": "kit", "value": "provedor"}, {"tag": "funcao", "value": "servidor"}],
                          f"Provedor Exemplo (fictício): {d['nome_visivel']}. Agente Zabbix 2 em container.",
                          [tpl[TPL_PROV["disp"]], tpl[TPL_PROV["linux"]]])
        hosts[d["host"]] = hid
        # container sem systemd: a descoberta de serviços não se aplica no lab
        for r in api.call("discoveryrule.get", {"hostids": [hid], "output": ["itemid"], "filter": {"key_": "systemd.unit.discovery[service]"}}):
            api.call("discoveryrule.update", {"itemid": r["itemid"], "status": 1})
    # o próprio Zabbix: troca o template oficial de saúde pela versão NOTO (mesmas chaves, não podem coexistir)
    zbx = api.call("host.get", {"output": ["hostid"], "filter": {"host": ["Zabbix server"]}, "selectParentTemplates": ["templateid", "host"]})
    if zbx:
        # sai também o "Linux by Zabbix agent": o container do Zabbix server não roda agente (seria um alerta falso no lab)
        oficial = [t["templateid"] for t in zbx[0]["parentTemplates"] if t["host"] in ("Zabbix server health", "Linux by Zabbix agent")]
        if oficial:
            api.call("host.update", {"hostid": zbx[0]["hostid"], "templates_clear": [{"templateid": t} for t in oficial]})
        if tpl[TPL_PROV["zabbix"]] not in [t["templateid"] for t in zbx[0]["parentTemplates"]]:
            api.call("host.massadd", {"hosts": [{"hostid": zbx[0]["hostid"]}], "templates": [{"templateid": tpl[TPL_PROV["zabbix"]]}]})
        api.call("host.massadd", {"hosts": [{"hostid": zbx[0]["hostid"]}], "groups": [{"groupid": groups[GROUP_SRV]}]})
    return hosts


def forca_descoberta(api, hostids):
    """Roda já as descobertas (LLD). No lab, recriar um container renumera as interfaces (ifIndex): sem isso,
    os nomes dos itens só seriam corrigidos na próxima descoberta (1 h)."""
    regras = api.call("discoveryrule.get", {"output": ["itemid"], "hostids": hostids, "filter": {"status": 0}})
    for i in range(0, len(regras), 50):
        with contextlib.suppress(LabError):
            api.call("task.create", [{"type": 6, "request": {"itemid": r["itemid"]}} for r in regras[i:i + 50]])


def check_now(api, hostids):
    """Pede ao Zabbix para coletar já os itens que ainda não têm valor.
    Só itens novos: uma coleta extra 1 s depois da normal distorce itens "por segundo" (tráfego)."""
    items = api.call("item.get", {"output": ["itemid", "lastclock"], "hostids": hostids, "filter": {"type": [3, 15, 20]},
                                  "templated": False, "webitems": False, "monitored": True})
    rules = api.call("discoveryrule.get", {"output": ["itemid", "lastclock"], "hostids": hostids})
    ids = [i["itemid"] for i in items + rules if i.get("lastclock", "0") == "0"]
    for start in range(0, len(ids), 50):
        with contextlib.suppress(LabError):
            api.call("task.create", [{"type": 6, "request": {"itemid": itemid}} for itemid in ids[start:start + 50]])


# ---------------------------------------------------------------- verificações

def snmp(env, address, *oids, walk=False):
    tool = "snmpwalk" if walk else "snmpget"
    result = dexec("noc", tool, "-v2c", "-c", env["LAB_SNMP_COMMUNITY"], "-t", "2", "-r", "1", "-Oqs", address, *oids)
    return result.stdout.strip() if result.returncode == 0 and "Timeout" not in result.stdout else None


def ping(address):
    return dexec("noc", "ping", "-c", "2", "-W", "1", "-q", address).returncode == 0


def wan_index(env, link):
    names = snmp(env, fw_ip(env, link), "IF-MIB::ifName", walk=True) or ""
    for line in names.splitlines():
        oid, _, value = line.partition(" ")
        if value == "wan1":
            return int(oid.rsplit(".", 1)[1])
    return None


def wait_snmp(env, kits, timeout=90):
    targets = ([ip(env, d["mgmt"]) for d in prov_equipamentos(True) if not equip_parado(d)] if "provedor" in kits else []) + \
              ([fw_ip(env, link) for link in msp_links()] if "msp" in kits else [])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(snmp(env, address, "SNMPv2-MIB::sysName.0") for address in targets):
            return
        time.sleep(2)
    raise LabError("Os agentes SNMP não responderam. Veja ./lab.sh logs <serviço>.")


def check_collection(env, kits):
    """Testa do namespace de rede do zabbix-server (container noc), o mesmo caminho do Zabbix."""
    ok = True
    scen = read_state().get("cenarios", {})
    if "provedor" in kits:
        for d in prov_equipamentos(True):
            addr = ip(env, d["mgmt"])
            name = snmp(env, addr, "SNMPv2-MIB::sysName.0")
            parado = equip_parado(d)
            detail = "sem resposta (esperado: equipamento parado)" if parado and not name else ("sem resposta" if not name else "")
            if name and d["papel"] == "borda":
                estados = (snmp(env, addr, "1.3.6.1.2.1.15.3.1.2", walk=True) or "").split("\n")
                ok_bgp = sum(1 for e in estados if e.endswith(" 6"))
                detail = f"BGP: {ok_bgp}/{len(d['bgp']['vizinhos'])} sessões estabelecidas"
            elif name and d["papel"] == "olt":
                onus = snmp(env, addr, "1.3.6.1.4.1.32473.1.3.1.2", walk=True) or ""
                lista = [l.rsplit(" ", 1)[-1] for l in onus.splitlines() if l.strip()]
                detail = f"{d['olt']['pons']} PONs, {lista.count('1')}/{len(lista)} ONUs online"
            elif name:
                aliases = snmp(env, addr, "IF-MIB::ifAlias", walk=True) or ""
                detail = f"{sum(1 for l in aliases.splitlines() if '|' in l)} enlaces com descrição"
            good = (name is None) if parado else bool(name)
            ok &= good
            say(f"  {'OK ' if good else 'ERRO'} {d['host']:<16} SNMP {addr:<14} {detail}")
        for d in prov_servidores():
            addr = ip(env, d["mgmt"])
            parado = equip_parado(d)
            alive = ping(addr)
            good = alive != parado
            ok &= good
            say(f"  {'OK ' if good else 'ERRO'} {d['host']:<16} ping {addr:<14} {'agente Zabbix 2' if alive else 'sem resposta'}")
    if "msp" in kits:
        for link in msp_links():
            cenario = scen.get(link["service"], {}).get("tipo", "normal")
            name = snmp(env, fw_ip(env, link), "SNMPv2-MIB::sysName.0")
            index = wan_index(env, link) if name else None
            expected_down = cenario == "fail"
            good = (name is None) if expected_down else (bool(name) and index is not None)
            ok &= good
            detail = "sem resposta (esperado: cenário fail)" if expected_down and not name else \
                     (f"wan1 = ifIndex {index}" if name else "sem resposta")
            say(f"  {'OK ' if good else 'ERRO'} {link['host']:<16} SNMP {fw_ip(env, link):<14} {detail}")
        for vpn in msp_vpns():
            expected_down = "fail" in (scen.get(vpn["service"], {}).get("tipo"), scen.get(vpn["fw"]["service"], {}).get("tipo"))
            alive = ping(vpn["ip"])
            good = alive != expected_down
            ok &= good
            say(f"  {'OK ' if good else 'ERRO'} {vpn['host']:<16} ping {vpn['ip']:<14} "
                f"{'sem resposta (esperado: cenário fail)' if expected_down else ('túnel ok' if alive else 'sem resposta')}")
    return ok


def item_values(api, hostids, **flt):
    return api.call("item.get", {"output": ["itemid", "hostid", "key_", "name", "lastvalue", "lastclock", "state", "error"],
                                 "hostids": hostids, "webitems": False, "monitored": True, **flt})


def check_items(api, kits, wait=True):
    """Itens com valor: equipamentos do provedor (por host), links MSP e VPNs."""
    state = read_state().get("hosts", {})
    failing = {name for name, s in read_state().get("cenarios", {}).items() if isinstance(s, dict) and s.get("tipo") == "fail"}
    deadline = time.monotonic() + (300 if wait else 0)
    ok = True
    while True:
        report = []
        ok = True
        if "provedor" in kits:
            check_now(api, [state[n] for n in prov_hosts() if n in state])
            for nome in prov_hosts():
                if nome not in state or equip_parado({"host": nome}):
                    continue
                its = item_values(api, [state[nome]], filter={"status": 0})
                com = sum(1 for i in its if i["lastclock"] != "0")
                sem_suporte = [i for i in its if i["state"] == "1"]
                # ONUs fora (LOS, sem energia) não têm leitura de sinal: o template descarta esse valor de propósito
                good = bool(its) and com >= 0.85 * len(its) and not sem_suporte
                ok &= good
                report.append(f"  {'OK ' if good else 'ERRO'} {nome:<16} {com}/{len(its)} itens com valor"
                              + (f"; sem suporte: {', '.join(i['key_'] for i in sem_suporte[:3])}" if sem_suporte else ""))
        if "msp" in kits:
            hostids = [state[link["host"]] for link in msp_links() if link["service"] not in failing]
            msp = item_values(api, hostids) if hostids else []
            msp_ok = sum(1 for i in msp if i["lastclock"] != "0")
            good = msp_ok == len(msp)
            ok &= good
            report.append(f"  {'OK ' if good else 'ERRO'} MSP (NOTO - LINK INTERNET): {msp_ok}/{len(msp)} itens com valor"
                          + (f" (fora da conta: {', '.join(sorted(failing))})" if failing else ""))
            report += [f"       não suportado: {i['key_']} - {i['error']}" for i in msp if i["state"] == "1"][:4]
            down = {vpn["service"] for vpn in msp_vpns() if vpn["service"] in failing or vpn["fw"]["service"] in failing}
            vpn_ids = [state[v["host"]] for v in msp_vpns() if v["host"] in state and v["service"] not in down]
            vpn_items = item_values(api, vpn_ids, filter={"status": 0}) if vpn_ids else []
            vpn_ok = sum(1 for i in vpn_items if i["lastclock"] != "0")
            good = vpn_ok == len(vpn_items)
            ok &= good
            report.append(f"  {'OK ' if good else 'ERRO'} VPNs (NOTO - VPN): {vpn_ok}/{len(vpn_items)} itens com valor")
        if ok or time.monotonic() > deadline:
            break
        time.sleep(10)
    for line in report:
        say(line)
    return ok


# ---------------------------------------------------------------- cenários (Kit NOC e Kit Provedor)
# Kit NOC : link MSP (FW, wan1) e VPN (lan1 do FW).
# Provedor: enlace (interface do lado cliente de um fluxo: transito-a, borda-bng, uplink-olt-norte...) e
#           equipamento inteiro (fail = container pausado: ping e SNMP param, como um equipamento desligado).
DEV = {"fw": "wan1"}
IPERF = {"fw": ("operadora", 5200)}


def prov_hosts():
    return [d["host"] for d in prov_equipamentos(True)] + [d["host"] for d in prov_servidores()]


def prov_alvos_equip():
    # gerência: "mgmt" nos equipamentos da topologia; "eth0" nos servidores (imagem do agente Zabbix)
    return [{"kind": "equip", "service": d["servico"], "host": d["host"], "nome": d.get("nome_visivel", d["host"]),
             "ifname": "mgmt" if "papel" in d else "eth0"} for d in prov_equipamentos(True) + prov_servidores()]


def equip_parado(d):
    servico = d.get("servico") or next((x["servico"] for x in prov_equipamentos(True) + prov_servidores() if x["host"] == d["host"]), "")
    return read_state().get("cenarios", {}).get(servico, {}).get("tipo") == "fail"


def targets(kits=None):
    kits = kits or active_kits()
    return (msp_links() + msp_vpns() if "msp" in kits else []) + (prov_enlaces() + prov_alvos_equip() if "provedor" in kits else [])


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def resolve_target(name):
    wanted = name.lower().strip()
    todos = targets()
    if not todos:
        raise LabError("Nenhum kit nesta instalação. Use ./lab.sh up --msp e/ou --provedor.")
    exact = [t for t in todos if wanted in (t["service"], t["host"].lower(), slug(t.get("nome", "")))]
    # trecho: "escola" = link; com "vpn" ("vpn-escola") = o túnel
    pool = [t for t in todos if (t["kind"] == "vpn") == ("vpn" in wanted)]
    key = slug(wanted.replace("vpn-", "").replace("vpn", ""))
    partial = [t for t in pool if key and (key in t["service"] or key in slug(t.get("nome", "")))]
    found = exact or partial
    if len(found) != 1:
        options = ", ".join(t["service"] for t in (found or todos))
        raise LabError(f"Alvo '{name}' {'ambíguo' if found else 'desconhecido'}. Opções: {options}")
    return found[0]


def fw_sh(link, script):
    result = dexec(link["service"], "sh", "-c", script)
    if result.returncode:
        raise LabError(f"{link['host']}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def netem_cmd(dev, scenario):
    loss, latency = scenario.get("loss", 0), scenario.get("latency", 0)
    netem = "netem limit 100000"
    if latency:
        netem += f" delay {latency}ms {max(1, latency // 10)}ms"
    if loss:
        netem += f" loss {loss}%"
    return f"tc qdisc add dev {dev} root {netem}"


def apply_scenario(link, scenario):
    tipo = scenario.get("tipo", "normal")
    if link["kind"] == "vpn":  # o túnel cai (LAN do FW sem IP), o link de internet continua no ar
        if tipo not in ("normal", "fail"):
            raise LabError("Para VPN só há fail e restore (perda/latência: aplique no link do FW).")
        # IP de interface em down continua respondendo ping no Linux: o túnel "cai" tirando o IP da LAN
        fw_sh(link["fw"], "ip addr flush dev lan1" if tipo == "fail" else f"ip addr replace {link['ip']}/24 dev lan1 && ip link set lan1 up")
        return
    if link["kind"] == "equip":
        if tipo not in ("normal", "fail"):
            raise LabError("Para um equipamento inteiro só há fail (fora da rede) e restore.")
        # corta o cabo da gerência: ping e SNMP param, como num equipamento desligado
        # (pausar o container não serve: o kernel continua respondendo ao ping)
        veth_carrier(link["service"], link["ifname"], tipo != "fail")
        return
    if link["kind"] == "enlace":
        dev, f = link["ifname"], link["fluxo"]
        cmds = [f"tc qdisc del dev {dev} root 2>/dev/null || true"]
        if tipo == "degrade":
            cmds.append(netem_cmd(dev, scenario))
        cmds.append(f"echo {'saturar' if tipo == 'saturate' else 'normal'} > /tmp/cenario-{f['id']}")
        # [i]perf3: o padrão não casa com o próprio "sh -c" (senão o pkill mata o shell que o executa)
        cmds.append(f"(pkill -f '[i]perf3 -c .* -p {f['porta']} ' ; pkill -f '[i]perf3 -c .* -p {f['porta'] + 1} ' ; true)")
        result = dexec(link["container"], "sh", "-c", " && ".join(cmds))
        if result.returncode:
            raise LabError(f"{link['host']}: {(result.stderr or result.stdout).strip()}")
        veth_carrier(link["container"], dev, tipo != "fail")
        dexec(link["servidor"], "sh", "-c", f"pkill -f '[i]perf3 -s -p {f['porta']} ' ; pkill -f '[i]perf3 -s -p {f['porta'] + 1} ' ; true")
        return
    dev = DEV[link["kind"]]
    cmds = [f"tc qdisc del dev {dev} root 2>/dev/null || true", f"ip link set {dev} up"]
    cenario_trafego = "normal"
    if tipo == "degrade":
        cmds.append(netem_cmd(dev, scenario))
    elif tipo == "saturate":
        cenario_trafego = "saturar"
    elif tipo == "fail":
        cmds.append(f"ip link set {dev} down")
    cmds.append(f"echo {cenario_trafego} > /tmp/lab-cenario")
    cmds.append("pkill iperf3 || true")  # o gerador recomeça já com a nova taxa
    fw_sh(link, " && ".join(cmds))
    # reinicia os 2 servidores iperf3 deste alvo: se a interface caiu no meio de um teste,
    # o servidor fica preso esperando um cliente que não existe mais
    server, base = IPERF[link["kind"]]
    n = link["n"]
    dexec(server, "sh", "-c", f"pkill -f '[i]perf3 -s -p {base + 2 * n - 1} ' ; pkill -f '[i]perf3 -s -p {base + 2 * n} ' ; true")


def veth_carrier(servico, ifname, up):
    """Enlace fora = fibra rompida: a interface perde a portadora (oper down) mas segue admin up, como no
    equipamento real. O veth não aceita "carrier off" e um "ip link set down" deixaria a porta admin down
    (a descoberta de interfaces ignora porta desligada de propósito). Por isso derrubamos a outra ponta do
    cabo: o veth par da interface, no host (container auxiliar com --network host, só esse veth)."""
    peer = dexec(servico, "cat", f"/sys/class/net/{ifname}/iflink")
    if peer.returncode or not peer.stdout.strip().isdigit():
        raise LabError(f"{servico}: não achei a interface {ifname}.")
    script = (f"n=$(ip -o link | awk -F': ' '$1=={peer.stdout.strip()} {{print $2}}' | cut -d@ -f1); "
              f"[ -n \"$n\" ] && ip link set dev \"$n\" {'up' if up else 'down'}")
    result = run(["docker", "run", "--rm", "--network", "host", "--cap-add", "NET_ADMIN", "--entrypoint", "sh",
                  f"{PROJECT}-node:1", "-c", script], capture=True, check=False)
    if result.returncode:
        raise LabError(f"{servico}: não consegui mudar a interface {ifname}: {(result.stderr or result.stdout).strip()}")


def set_scenario(link, scenario):
    state = read_state()
    scenarios = state.setdefault("cenarios", {})
    if scenario.get("tipo", "normal") == "normal":
        scenarios.pop(link["service"], None)
    else:
        scenarios[link["service"]] = scenario
    apply_scenario(link, scenario)
    save_state(state)


def reapply_scenarios(kits=None):
    for link in targets(kits):
        with contextlib.suppress(LabError):
            apply_scenario(link, read_state().get("cenarios", {}).get(link["service"], {"tipo": "normal"}))
    if "provedor" in (kits or active_kits()):
        for d in prov_equipamentos(True):
            if d["papel"] == "olt":
                with contextlib.suppress(LabError):
                    aplica_olt(d["servico"])
        for peer in [d for d in prov_equipamentos() if d["papel"] == "upstream"]:
            with contextlib.suppress(LabError):
                aplica_bgp(peer["servico"])


def describe(scenario, kind="fw"):
    tipo = scenario.get("tipo", "normal")
    if tipo == "degrade":
        parts = [f"perda {scenario['loss']}%" if scenario.get("loss") else "", f"latência +{scenario['latency']} ms" if scenario.get("latency") else ""]
        return "DEGRADADO: " + ", ".join(p for p in parts if p)
    fora = {"enlace": "fibra rompida", "equip": "equipamento fora da rede"}.get(kind) or f"{DEV.get(kind, 'interface')} down"
    return {"normal": "normal", "saturate": "SATURADO (~112% do contratado)", "fail": f"FORA ({fora})"}[tipo]


def parse_degrade(params):
    opts = dict(zip(params[0::2], params[1::2]))
    if not opts or set(opts) - {"loss", "latency"} or len(params) % 2:
        raise LabError("Uso: ./lab.sh degrade <alvo> loss <%> latency <ms>   (pode usar só um dos dois)")
    try:
        loss = float(opts.get("loss", 0))
        latency = int(opts.get("latency", 0))
    except ValueError as error:
        raise LabError("loss e latency devem ser números.") from error
    if not 0 <= loss <= 100 or not 0 <= latency <= 10000:
        raise LabError("loss entre 0 e 100; latency entre 0 e 10000 ms.")
    return {"tipo": "degrade", "loss": int(loss) if loss.is_integer() else loss, "latency": latency}


def cmd_degrade(args):
    scenario = parse_degrade(args.params)
    link = resolve_target(args.link)
    set_scenario(link, scenario)
    onde = "O tráfego do enlace (e o BGP, se a perda for alta)" if link["kind"] == "enlace" else "Os alertas"
    say(f"{link['host']}: {describe(scenario)}. {onde} aparecem em ~2 a 5 min. Desfaça com ./lab.sh restore {link['service']}.")


def cmd_saturate(args):
    link = resolve_target(args.link)
    set_scenario(link, {"tipo": "saturate"})
    say(f"{link['host']}: tráfego indo para ~112% de {link['contratado']} Mbps. Desfaça com ./lab.sh restore {link['service']}.")


# ---------------------------------------------------------------- cenários exclusivos do Kit Provedor: BGP, ONU e PON

def require_provedor():
    if "provedor" not in active_kits():
        raise LabError("O Kit Provedor não está nesta instalação. Use ./lab.sh up --provedor.")


def upstream(nome):
    ups = [d for d in prov_equipamentos() if d["papel"] == "upstream"]
    achou = [d for d in ups if slug(nome) in (d["servico"], slug(d["host"])) or slug(nome) in d["servico"]]
    if len(achou) != 1:
        raise LabError(f"Peer BGP '{nome}' desconhecido. Opções: {', '.join(d['servico'] for d in ups)}")
    return achou[0]


def aplica_bgp(servico):
    d = next(x for x in prov_equipamentos() if x["servico"] == servico)
    env = read_env()
    viz = ip(env, d["bgp"]["vizinhos"][0]["ip"])
    down = read_state().get("cenarios", {}).get(f"bgp:{servico}") == "down"
    r = dexec(servico, "vtysh", "--vty_socket", "/tmp/frr", "-c", "configure terminal", "-c", f"router bgp {d['bgp']['asn']}",
              "-c", f"{'' if down else 'no '}neighbor {viz} shutdown")
    if r.returncode:
        raise LabError(f"{d['host']}: {(r.stderr or r.stdout).strip()}")


def cmd_bgp(args):
    require_provedor()
    d = upstream(args.peer)
    if args.estado not in ("down", "up"):
        raise LabError("Uso: ./lab.sh bgp <transito-a|transito-b|ix-sp> <down|up>")
    state = read_state()
    cen = state.setdefault("cenarios", {})
    if args.estado == "down":
        cen[f"bgp:{d['servico']}"] = "down"
    else:
        cen.pop(f"bgp:{d['servico']}", None)
    save_state(state)
    aplica_bgp(d["servico"])
    say(f"BGP com {d['host']} (AS{d['bgp']['asn']}): {'sessão derrubada pelo peer (o enlace continua no ar)' if args.estado == 'down' else 'sessão liberada'}. "
        f"O Zabbix vê em ~1 min ('Sessão BGP com ... caiu').")


def olt_alvo(nome):
    olts = [d for d in prov_equipamentos(True) if d["papel"] == "olt"]
    achou = [d for d in olts if slug(nome) in d["servico"]]
    if len(achou) != 1:
        raise LabError(f"OLT '{nome}' desconhecida. Opções: {', '.join(d['servico'] for d in olts)}")
    return achou[0]


def aplica_olt(servico):
    cen = read_state().get("cenarios", {}).get(f"olt:{servico}", {})
    dados = json.dumps({"onus": cen.get("onus", {}), "pons": cen.get("pons", {})})
    r = dexec(servico, "sh", "-c", f"cat > /tmp/olt-cenario.json <<'FIM'\n{dados}\nFIM")
    if r.returncode:
        raise LabError(f"{servico}: {(r.stderr or r.stdout).strip()}")


ESTADOS_ONU = {"los": "LOS (fibra rompida)", "energia": "sem energia (dying gasp)", "offline": "offline", "auth": "falha de autenticação",
               "desativada": "desativada", "rx-baixo": "sinal RX baixo (-26,5 dBm)", "rx-critico": "sinal RX crítico (-28,2 dBm)",
               "normal": "normal"}


def cmd_onu(args):
    require_provedor()
    d = olt_alvo(args.olt)
    if args.estado not in ESTADOS_ONU:
        raise LabError(f"Estado inválido. Opções: {', '.join(ESTADOS_ONU)}")
    m = re.fullmatch(r"(\d+)[./](\d+)", args.onu)
    if not m or not (1 <= int(m[1]) <= d["olt"]["pons"] and 1 <= int(m[2]) <= d["olt"]["onus_por_pon"]):
        raise LabError(f"ONU no formato <pon>.<onu>, ex.: 2.5 (PONs 1-{d['olt']['pons']}, ONUs 1-{d['olt']['onus_por_pon']}).")
    chave = f"{int(m[1])}.{int(m[2])}"
    state = read_state()
    cen = state.setdefault("cenarios", {}).setdefault(f"olt:{d['servico']}", {"onus": {}, "pons": {}})
    if args.estado == "normal":
        # "normal" também cobre as anomalias fixas da topologia (vira online com sinal bom)
        cen["onus"][chave] = "normal"
    else:
        cen["onus"][chave] = args.estado
    save_state(state)
    aplica_olt(d["servico"])
    say(f"{d['host']} ONU {chave}: {ESTADOS_ONU[args.estado]}. O Zabbix vê na próxima coleta (status a cada 3 min, sinal a cada 5 min).")


def cmd_pon(args):
    require_provedor()
    d = olt_alvo(args.olt)
    if args.estado not in ("down", "up") or not args.pon.isdigit() or not 1 <= int(args.pon) <= d["olt"]["pons"]:
        raise LabError(f"Uso: ./lab.sh pon <olt> <1-{d['olt']['pons']}> <down|up>")
    state = read_state()
    cen = state.setdefault("cenarios", {}).setdefault(f"olt:{d['servico']}", {"onus": {}, "pons": {}})
    if args.estado == "down":
        cen["pons"][args.pon] = "down"
    else:
        cen["pons"].pop(args.pon, None)
    save_state(state)
    aplica_olt(d["servico"])
    say(f"{d['host']} PON {args.pon}: {'fora (todas as ONUs dela em LOS)' if args.estado == 'down' else 'normal'}.")


def cmd_fail(args):
    link = resolve_target(args.link)
    set_scenario(link, {"tipo": "fail"})
    if link["kind"] == "vpn":
        say(f"{link['host']}: túnel fora (LAN atrás do {link['fw']['host']} sem resposta). "
            f"'Sem resposta de Ping' abre em ~2 min. Desfaça com ./lab.sh restore {link['service']}.")
    elif link["kind"] == "enlace":
        bgp = " A sessão BGP cai em ~10 s." if link["service"] in ("transito-a", "transito-b", "ix") else ""
        say(f"{link['host']} ({link['nome']}): fibra rompida (oper down, admin up); o tráfego zera.{bgp} "
            f"Desfaça com ./lab.sh restore {link['service']}.")
    elif link["kind"] == "equip":
        say(f"{link['host']}: fora da rede (ping e SNMP param). Na cascata, abre só 'Sem resposta de ping' em ~3 min. "
            f"Desfaça com ./lab.sh restore {link['service']}.")
    else:
        say(f"{link['host']}: wan1 derrubada. Ping e SNMP param; 'Sem resposta de Ping' abre em ~2 min. "
            f"Desfaça com ./lab.sh restore {link['service']}.")


def cmd_restore(args):
    link = resolve_target(args.link)
    set_scenario(link, {"tipo": "normal"})
    say(f"{link['host']}: normal.")


def cmd_reset(args):
    for link in targets():
        set_scenario(link, {"tipo": "normal"})
    if "provedor" in active_kits():
        state = read_state()
        state["cenarios"] = {k: v for k, v in state.get("cenarios", {}).items() if not k.startswith(("bgp:", "olt:"))}
        save_state(state)
        reapply_scenarios(["provedor"])
    say("Todos os alvos de volta ao normal (histórico preservado).")


# ---------------------------------------------------------------- comandos

def cmd_up(args):
    require_docker()
    env = initialize()
    chosen = [kit for kit in KITS if getattr(args, kit)]
    if chosen:
        set_env("LAB_KITS", ",".join(chosen))
    elif "LAB_KITS" not in env:
        set_env("LAB_KITS", ",".join(KITS))
    env = read_env()
    kits = active_kits(env)
    names = {"msp": "Kit NOC (MSP)", "provedor": "Kit Provedor"}
    say(f"Kits desta instalação: {', '.join(names[k] for k in kits)}")
    for kit in KITS:
        if kit not in kits:
            compose("rm", "-sf", *kit_services(kit), kits=KITS, capture=True, check=False)
    say("Construindo a imagem dos nós e subindo os containers...")
    compose("build", "noc")   # uma imagem para todos os nós: construir por serviço gera IDs diferentes e recria tudo
    compose("up", "-d", "--remove-orphans")
    # cenários saem durante o cadastro (precisa do SNMP) e voltam no fim
    for link in targets(kits):
        with contextlib.suppress(LabError):
            apply_scenario(link, {"tipo": "normal"})
    say("Aguardando o Zabbix...")
    api, version = wait_for_api(env)
    wait_snmp(env, kits)
    say(f"Zabbix {version} pronto. Importando templates e cadastrando hosts...")
    hosts = provision(api, env, kits)
    state = read_state()
    state["hosts"] = {**state.get("hosts", {}), **hosts}
    save_state(state)
    reapply_scenarios(kits)
    check_now(api, list(hosts.values()))
    if "provedor" in kits:
        forca_descoberta(api, [hosts[n] for n in prov_hosts() if n in hosts])
    say("Verificando a rede a partir do namespace do Zabbix:")
    ok = check_collection(env, kits)
    say("Verificando os itens no Zabbix (a primeira descoberta leva ~1 minuto):")
    ok &= check_items(api, kits, wait=not args.no_wait)
    # itens criados pela descoberta com intervalo longo (ex.: AS remoto do BGP, 1 h) ganham a 1ª coleta agora;
    # antes, espera o Zabbix carregar os itens novos no cache (ZBX_CACHEUPDATEFREQUENCY = 10 s)
    time.sleep(15)
    check_now(api, list(hosts.values()))
    show_access(env)
    if not ok:
        raise LabError("Algo ainda não coletou. Rode ./lab.sh status em 1 minuto ou veja ./lab.sh logs <serviço>.")


def ip_da_maquina():
    """IP pelo qual esta máquina sai para a rede (nenhum pacote é enviado: só consulta a rota)."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    return "IP-desta-maquina"


def show_access(env):
    bind = env.get("LAB_BIND", "127.0.0.1")
    # 0.0.0.0 = aberto na rede (VM/servidor): mostra o IP real, que é o que se digita no navegador de outro computador
    host = "localhost" if bind == "127.0.0.1" else ip_da_maquina() if bind == "0.0.0.0" else bind
    say()
    say(f"Zabbix : http://{host}:{env['LAB_ZBX_PORT']}   usuário Admin   senha {env['LAB_ADMIN_PASSWORD']}")
    say(f"Grafana: http://{host}:{env['LAB_GRAFANA_PORT']}   usuário admin   senha {env['LAB_GRAFANA_PASSWORD']}")


def human_bps(value):
    value = float(value or 0)
    for unit, div in (("Gbps", 1e9), ("Mbps", 1e6), ("Kbps", 1e3)):
        if value >= div:
            return f"{value / div:.1f} {unit}"
    return f"{value:.0f} bps"


def msp_overview(api):
    state = read_state()
    say("Links MSP (últimos valores no Zabbix):")
    say(f"  {'LINK':<16} {'CONTRATO':>8} {'DOWNLOAD':>11} {'USO':>5} {'UPLOAD':>11} {'PERDA':>6} {'LAT.':>8}  CENÁRIO")
    for link in msp_links():
        hostid = state.get("hosts", {}).get(link["host"])
        values = {i["key_"]: i["lastvalue"] for i in item_values(api, [hostid])} if hostid else {}
        down = float(values.get("net.if.in.linkprincipal") or 0)
        use = 100 * down / (link["contratado"] * 1e6)
        alive = values.get("icmpping") == "1"
        say(f"  {link['host']:<16} {link['contratado']:>5} M {human_bps(down):>11} {use:>4.0f}% "
            f"{human_bps(values.get('net.if.out.linkprincipal')):>11} "
            f"{float(values.get('icmppingloss') or 0):>5.0f}% {float(values.get('icmppingsec') or 0):>6.1f}ms  "
            f"{describe(state.get('cenarios', {}).get(link['service'], {}))}{'' if alive else '  [sem ping]'}")


def provider_overview(api):
    """Enlaces, BGP e OLTs do Kit Provedor, lidos do Zabbix (como os dashboards vão ler)."""
    state = read_state()
    hosts, cen = state.get("hosts", {}), state.get("cenarios", {})
    say("Enlaces do provedor (interface do lado cliente, NOTO TI - Interfaces SNMP):")
    say(f"  {'ENLACE':<18} {'INTERFACE':<26} {'CONTRATO':>8} {'ENTRADA':>11} {'USO':>5} {'SAÍDA':>11} {'STATUS':>6}  CENÁRIO")
    for e in prov_enlaces():
        dono = e["host"].split()[0]
        its = {i["name"]: i for i in item_values(api, [hosts[dono]], search={"name": f"Interface {e['ifname']} "})} if dono in hosts else {}
        val = lambda sufixo: next((i["lastvalue"] for n, i in its.items() if n.endswith(sufixo)), None)
        down, up, st = float(val("tráfego de entrada") or 0), float(val("tráfego de saída") or 0), val("status operacional")
        say(f"  {e['service']:<18} {e['host']:<26} {e['contratado']:>5} M {human_bps(down):>11} {100 * down / (e['contratado'] * 1e6):>4.0f}% "
            f"{human_bps(up):>11} {'up' if st == '1' else 'down' if st else '?':>6}  {describe(cen.get(e['service'], {}), 'enlace')}")
    if "BORDA-01" in hosts:
        peers = item_values(api, [hosts["BORDA-01"]], search={"key_": "BgpPeerState["})
        asn = {i["key_"].split("[")[1].rstrip("]"): i["lastvalue"] for i in item_values(api, [hosts["BORDA-01"]], search={"key_": "BgpPeerRemoteAs["})}
        estados = {"1": "idle", "2": "connect", "3": "active", "4": "opensent", "5": "openconfirm", "6": "established"}
        say("BGP da BORDA-01 (NOTO TI - BGP): " + ("; ".join(
            f"{i['key_'].split('[')[1].rstrip(']')} AS{asn.get(i['key_'].split('[')[1].rstrip(']'), '?')} {estados.get(i['lastvalue'], i['lastvalue'] or '?')}"
            for i in sorted(peers, key=lambda x: x["key_"])) or "sem peers descobertos ainda"))
    for d in prov_equipamentos(True):
        if d["papel"] != "olt" or d["host"] not in hosts:
            continue
        st = item_values(api, [hosts[d["host"]]], search={"key_": "olt.onu.status["})
        cont = {}
        for i in st:
            cont[i["lastvalue"]] = cont.get(i["lastvalue"], 0) + 1
        nomes = {"1": "online", "2": "LOS", "3": "sem energia", "4": "offline", "5": "autenticação", "6": "desativada"}
        resumo = ", ".join(f"{v} {nomes.get(k, k or 'sem dado')}" for k, v in sorted(cont.items()))
        c = cen.get(f"olt:{d['servico']}", {})
        extra = [f"ONU {k}: {v}" for k, v in c.get("onus", {}).items()] + [f"PON {k}: down" for k in c.get("pons", {})]
        say(f"{d['host']} (NOTO TI - OLT Lab): {len(st)} ONUs: {resumo or 'sem dados ainda'}" + (f"  [cenário: {'; '.join(extra)}]" if extra else ""))


def cmd_status(args):
    require_docker()
    env = read_env()
    kits = active_kits(env)
    result = compose("ps", "--format", "json", capture=True)
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        say("Laboratório parado. Use ./lab.sh up.")
        return
    bad = [row["Service"] for row in rows if row.get("State") != "running" or row.get("Health") not in ("", "healthy")]
    say(f"Kits: {', '.join(kits)} | containers: {len(rows)} em execução" + (f"; com problema: {', '.join(bad)}" if bad else ""))
    check_collection(env, kits)
    if read_state().get("hosts"):
        api = Api(env).login()
        say("Itens no Zabbix:")
        check_items(api, kits, wait=False)
        if "msp" in kits:
            msp_overview(api)
        if "provedor" in kits:
            provider_overview(api)
    show_access(env)


def cmd_down(args):
    require_docker()
    read_env()
    compose("down", kits=KITS)
    say("Laboratório parado. Dados do Zabbix e do Grafana preservados (./lab.sh up para voltar).")


def cmd_destroy(args):
    require_docker()
    say("ATENÇÃO: isto APAGA o laboratório inteiro: containers, Zabbix (hosts, histórico), Grafana (dashboards) e o .env.")
    say("Os dados apagados NÃO poderão ser recuperados.")
    try:
        resposta = input("Tem certeza? Digite APAGAR para confirmar ou Enter para cancelar: ").strip()
    except EOFError:
        resposta = ""
    if resposta != "APAGAR":
        raise LabError("Cancelado. Nada foi apagado.")
    if ENV_PATH.exists():
        compose("down", "-v", "--remove-orphans", kits=KITS)
    for path in (ENV_PATH, STATE_PATH):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    say("Laboratório removido (containers, volumes, .env).")


def cmd_logs(args):
    read_env()
    compose("logs", "--tail", "100", *([args.service] if args.service else []), kits=KITS)


def cmd_creds(args):
    show_access(read_env())


def cmd_snmpwalk(args):
    """Mostra o que o Zabbix enxerga via SNMP, ex.: ./lab.sh snmpwalk FW-INDUSTRIA-A ifName"""
    env = read_env()
    targets = {**{d["host"].lower(): ip(env, d["mgmt"]) for d in prov_equipamentos(True)},
               **{link["host"].lower(): fw_ip(env, link) for link in msp_links()}}
    address = targets.get(args.host.lower())
    if not address:
        raise LabError(f"Host desconhecido. Opções: {', '.join(sorted(targets))}")
    say(snmp(env, address, args.oid, walk=True) or "sem resposta")


def main():
    parser = argparse.ArgumentParser(prog="lab.sh", description="Lab NOTO TI - dashboards de links (Kit NOC e Kit Provedor)")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="sobe tudo e cadastra no Zabbix")
    up.add_argument("--msp", action="store_true", help="instala o Kit NOC (links de internet MSP)")
    up.add_argument("--provedor", action="store_true", help="instala o Kit Provedor (topologia de provedor com os templates NOTO)")
    up.add_argument("--no-wait", action="store_true", help="não espera a primeira coleta")
    up.set_defaults(func=cmd_up)
    sub.add_parser("down", help="para os containers (mantém os dados)").set_defaults(func=cmd_down)
    sub.add_parser("status", help="containers, coleta, cenários e acessos").set_defaults(func=cmd_status)
    sub.add_parser("reset", help="volta todos os links ao normal (mantém o histórico)").set_defaults(func=cmd_reset)
    sub.add_parser("destroy", help="apaga tudo, inclusive o histórico").set_defaults(func=cmd_destroy)
    sub.add_parser("creds", help="mostra URLs e senhas").set_defaults(func=cmd_creds)
    degrade = sub.add_parser("degrade", help="perda e/ou latência no link: degrade <link> loss <%%> latency <ms>")
    degrade.add_argument("link")
    degrade.add_argument("params", nargs="*")
    degrade.set_defaults(func=cmd_degrade)
    for name, func, text in (("saturate", cmd_saturate, "tráfego acima do contratado"),
                             ("fail", cmd_fail, "derruba um link/enlace (fibra rompida), uma VPN ou um equipamento inteiro do provedor"),
                             ("restore", cmd_restore, "volta o link ao normal")):
        p = sub.add_parser(name, help=text)
        p.add_argument("link")
        p.set_defaults(func=func)
    bgp = sub.add_parser("bgp", help="Kit Provedor: derruba/libera a sessão BGP com um peer: bgp <transito-a|transito-b|ix-sp> <down|up>")
    bgp.add_argument("peer")
    bgp.add_argument("estado")
    bgp.set_defaults(func=cmd_bgp)
    onu = sub.add_parser("onu", help="Kit Provedor: onu <olt> <pon.onu> <los|energia|offline|auth|desativada|rx-baixo|rx-critico|normal>")
    onu.add_argument("olt")
    onu.add_argument("onu")
    onu.add_argument("estado")
    onu.set_defaults(func=cmd_onu)
    pon = sub.add_parser("pon", help="Kit Provedor: pon <olt> <n> <down|up>")
    pon.add_argument("olt")
    pon.add_argument("pon")
    pon.add_argument("estado")
    pon.set_defaults(func=cmd_pon)
    logs = sub.add_parser("logs", help="logs de um serviço")
    logs.add_argument("service", nargs="?")
    logs.set_defaults(func=cmd_logs)
    walk = sub.add_parser("snmpwalk", help="SNMP walk visto da rede do Zabbix")
    walk.add_argument("host")
    walk.add_argument("oid", nargs="?", default="IF-MIB::ifName")
    walk.set_defaults(func=cmd_snmpwalk)
    args = parser.parse_args()
    try:
        args.func(args)
    except LabError as error:
        say(f"ERRO: {error}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
