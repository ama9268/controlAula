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
PUERTO_SSH = 22


class ErrorConfig(ValueError):
    """Error de configuración o de datos introducidos por el profesor."""


@dataclass
class Aula:
    clave: str
    nombre: str
    inicio: str
    fin: str
    profesor: str


@dataclass
class Config:
    usuario: str = "profesor"
    permitidas: List[str] = field(default_factory=lambda: ["10.0.0.4", "10.0.0.2"])
    horas: float = 4
    puertos: List[int] = field(default_factory=lambda: [22, 11100])
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


def validar_ip(texto: str) -> str:
    try:
        ip = ipaddress.IPv4Address(texto.strip())
    except ValueError:
        raise ErrorConfig(f"IP no válida: '{texto}'") from None
    return str(ip)


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
    return Objetivo(ips, profesor, f"{nombre}{ips[0]} – {ips[-1]} ({len(ips)} equipos)")


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
            cfg.permitidas = [validar_ip(x) for x in _lista(general["permitidas"])]
        if "horas_autodesbloqueo" in general:
            cfg.horas = validar_horas(float(general["horas_autodesbloqueo"]))
        if "puertos_entrada" in general:
            cfg.puertos = [int(x) for x in _lista(general["puertos_entrada"])]
        if "paralelo" in general:
            cfg.paralelo = int(general["paralelo"])
    except (configparser.Error, ValueError) as e:
        raise ErrorConfig(f"{ruta}: {e}") from None

    if not RE_USUARIO.match(cfg.usuario):
        raise ErrorConfig(f"{ruta}: usuario no válido: '{cfg.usuario}'")
    if any(not 1 <= p <= 65535 for p in cfg.puertos):
        raise ErrorConfig(f"{ruta}: puerto fuera de rango")
    if PUERTO_SSH not in cfg.puertos:
        cfg.puertos.insert(0, PUERTO_SSH)  # sin SSH se perdería el control del equipo
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
        except KeyError as e:
            raise ErrorConfig(f"{ruta}: falta {e} en el aula [{seccion}]") from None
        except ErrorConfig as e:
            raise ErrorConfig(f"{ruta}: aula [{seccion}]: {e}") from None
        cfg.aulas[seccion] = Aula(seccion, s.get("nombre", seccion).strip(), inicio, fin, profesor)
    return cfg
