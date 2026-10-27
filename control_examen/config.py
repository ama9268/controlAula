"""Configuración (aulas.ini) y cálculo de los equipos sobre los que actuar."""
from __future__ import annotations

import configparser
import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

RUTA_POR_DEFECTO = Path.home() / ".config" / "control-examen" / "aulas.ini"
RE_USUARIO = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
MAX_HORAS = 24
PUERTO_SSH = "22/tcp"
RE_SALIDA = re.compile(r"^(?P<destino>[0-9.]+(?:/[0-9]{1,2})?)(?::(?P<puerto>[0-9]{1,5})(?:/(?P<proto>tcp|udp))?)?$")
RE_ENTRADA = re.compile(r"^(?P<puerto>[0-9]{1,5})(?:/(?P<proto>tcp|udp))?$")


class ErrorConfig(ValueError):
    """Error de configuración o de datos introducidos por el profesor."""


@dataclass
class Aula:
    clave: str
    nombre: str
    inicio: str
    fin: str
    profesor: str
    # None: usa los valores de [general]. Si se indican, los sustituyen.
    permitidas: Optional[List[str]] = None
    puertos: Optional[List[str]] = None


@dataclass
class Config:
    usuario: str = "profesor"
    permitidas: List[str] = field(default_factory=lambda: ["10.0.0.4", "10.0.0.2"])
    horas: float = 4
    puertos: List[str] = field(default_factory=lambda: ["22/tcp", "11100/tcp"])
    paralelo: int = 30
    aulas: Dict[str, Aula] = field(default_factory=dict)

    def buscar_aula(self, texto: str) -> Aula:
        """Busca un aula por su clave o por su nombre (sin distinguir mayúsculas)."""
        texto = texto.strip().lower()
        for aula in self.aulas.values():
            if texto in (aula.clave.lower(), aula.nombre.lower()):
                return aula
        raise ErrorConfig(f"No existe el aula '{texto}'")


@dataclass
class Objetivo:
    """Equipos sobre los que se actúa y PC del profesor que se permite."""
    ips: List[str]
    profesor: str
    descripcion: str
    permitidas: List[str] = field(default_factory=list)  # destinos de salida
    puertos: List[str] = field(default_factory=list)     # puertos de entrada


def validar_ip(texto: str) -> str:
    try:
        ip = ipaddress.IPv4Address(texto.strip())
    except ValueError:
        raise ErrorConfig(f"IP no válida: '{texto}'") from None
    return str(ip)


def _puerto(texto: str, original: str) -> int:
    puerto = int(texto)
    if not 1 <= puerto <= 65535:
        raise ErrorConfig(f"Puerto fuera de rango en '{original}'")
    return puerto


def validar_salida(texto: str) -> str:
    """Destino de salida: IP, IP/prefijo, IP:puerto o IP:puerto/tcp|udp (se normaliza)."""
    texto = texto.strip()
    m = RE_SALIDA.match(texto)
    try:
        if not m:
            raise ValueError
        red = ipaddress.IPv4Network(m.group("destino"), strict=True)
    except ValueError:
        raise ErrorConfig(f"Destino permitido no válido: '{texto}' "
                          "(usa IP, IP/prefijo o IP:puerto[/tcp|udp])") from None
    destino = str(red.network_address) if red.prefixlen == 32 else str(red)
    if m.group("puerto"):
        destino += f":{_puerto(m.group('puerto'), texto)}/{m.group('proto') or 'tcp'}"
    return destino


def validar_entrada(texto: str) -> str:
    """Puerto de entrada: puerto o puerto/tcp|udp (se normaliza a puerto/proto)."""
    texto = texto.strip()
    m = RE_ENTRADA.match(texto)
    if not m:
        raise ErrorConfig(f"Puerto de entrada no válido: '{texto}' (usa puerto o puerto/tcp|udp)")
    return f"{_puerto(m.group('puerto'), texto)}/{m.group('proto') or 'tcp'}"


def lista_salidas(texto: str) -> List[str]:
    lista = list(dict.fromkeys(validar_salida(x) for x in _lista(texto)))
    if not lista:
        raise ErrorConfig("La lista de destinos permitidos está vacía")
    return lista


def lista_entradas(texto: str) -> List[str]:
    """Lista de puertos de entrada; añade SSH siempre (sin él se perdería el control)."""
    lista = list(dict.fromkeys(validar_entrada(x) for x in _lista(texto)))
    if PUERTO_SSH not in lista:
        lista.insert(0, PUERTO_SSH)
    return lista


def validar_horas(horas: float) -> float:
    if not 0 < horas <= MAX_HORAS:
        raise ErrorConfig(f"Las horas de autodesbloqueo deben estar entre 0 y {MAX_HORAS}")
    return horas


def a_minutos(horas: float) -> int:
    return max(1, round(validar_horas(horas) * 60))


def profesor_por_defecto(ip: str) -> str:
    red = ipaddress.ip_network(f"{validar_ip(ip)}/24", strict=False)
    return str(red.network_address + 253)


def expandir_rango(inicio: str, fin: str, excluir=()) -> List[str]:
    """IPs de inicio a fin (ambas incluidas, misma /24), sin las de `excluir`."""
    a = ipaddress.IPv4Address(validar_ip(inicio))
    b = ipaddress.IPv4Address(validar_ip(fin))
    if b < a:
        raise ErrorConfig("La IP final es menor que la inicial")
    if b not in ipaddress.ip_network(f"{a}/24", strict=False):
        raise ErrorConfig("Inicio y fin deben estar en la misma red /24")
    excluidas = set(excluir)
    return [str(ipaddress.IPv4Address(n)) for n in range(int(a), int(b) + 1)
            if str(ipaddress.IPv4Address(n)) not in excluidas]


def ips_locales() -> set:
    """IPs de este PC (vacío si no se pueden averiguar)."""
    try:
        salida = subprocess.run(["hostname", "-I"], capture_output=True, text=True,
                                timeout=2, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(salida.split())


def resolver_objetivo(cfg: Config, aula: Optional[str] = None, inicio: str = "",
                      fin: str = "", profesor: str = "") -> Objetivo:
    """Combina aula y rango manual (el rango manual tiene prioridad)."""
    datos = cfg.buscar_aula(aula) if aula else None
    inicio = inicio.strip() or (datos.inicio if datos else "")
    fin = fin.strip() or (datos.fin if datos else "")
    if not inicio or not fin:
        raise ErrorConfig("Indica un aula o una IP inicial y final")
    if not profesor.strip():
        profesor = datos.profesor if datos else profesor_por_defecto(inicio)
    profesor = validar_ip(profesor)

    # Nunca actuar sobre el propio PC del profesor.
    ips = expandir_rango(inicio, fin, excluir=ips_locales() | {profesor})
    if not ips:
        raise ErrorConfig("El rango no contiene ningún equipo de alumno")
    nombre = f"{datos.nombre}: " if datos else ""
    permitidas = datos.permitidas if datos and datos.permitidas is not None else cfg.permitidas
    puertos = datos.puertos if datos and datos.puertos is not None else cfg.puertos
    return Objetivo(ips, profesor, f"{nombre}{ips[0]} – {ips[-1]} ({len(ips)} equipos)",
                    list(permitidas), list(puertos))


def _lista(texto: str) -> List[str]:
    return [x.strip() for x in texto.split(",") if x.strip()]


def cargar(ruta: Optional[os.PathLike] = None) -> Config:
    """Lee aulas.ini. Si no existe, usa los valores por defecto sin aulas."""
    ruta = Path(ruta or os.environ.get("CONTROL_EXAMEN_CONFIG") or RUTA_POR_DEFECTO)
    cfg = Config()
    if not ruta.exists():
        return cfg

    ini = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    try:
        ini.read(ruta, encoding="utf-8")
        general = ini["general"] if ini.has_section("general") else {}
        cfg.usuario = general.get("usuario", cfg.usuario).strip()
        if "permitidas" in general:
            cfg.permitidas = lista_salidas(general["permitidas"])
        if "horas_autodesbloqueo" in general:
            cfg.horas = validar_horas(float(general["horas_autodesbloqueo"]))
        if "puertos_entrada" in general:
            cfg.puertos = lista_entradas(general["puertos_entrada"])
        if "paralelo" in general:
            cfg.paralelo = int(general["paralelo"])
    except (configparser.Error, ValueError) as e:
        raise ErrorConfig(f"{ruta}: {e}") from None

    if not RE_USUARIO.match(cfg.usuario):
        raise ErrorConfig(f"{ruta}: usuario no válido: '{cfg.usuario}'")
    if not 1 <= cfg.paralelo <= 100:
        raise ErrorConfig(f"{ruta}: 'paralelo' debe estar entre 1 y 100")

    for seccion in ini.sections():
        if seccion == "general":
            continue
        s = ini[seccion]
        try:
            inicio, fin = validar_ip(s["inicio"]), validar_ip(s["fin"])
            expandir_rango(inicio, fin)
            profesor = validar_ip(s.get("profesor") or profesor_por_defecto(inicio))
            permitidas = lista_salidas(s["permitidas"]) if "permitidas" in s else None
            puertos = lista_entradas(s["puertos_entrada"]) if "puertos_entrada" in s else None
        except KeyError as e:
            raise ErrorConfig(f"{ruta}: falta {e} en el aula [{seccion}]") from None
        except ErrorConfig as e:
            raise ErrorConfig(f"{ruta}: aula [{seccion}]: {e}") from None
        cfg.aulas[seccion] = Aula(seccion, s.get("nombre", seccion).strip(), inicio, fin, profesor,
                                 permitidas, puertos)
    return cfg
