"""Ventana Tkinter para el profesor."""
from __future__ import annotations

import queue
import threading
from dataclasses import replace
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import List, Optional

from . import acciones, config
from .editor import Editor
from .config import Config, ErrorConfig, Objetivo
from .remoto import Resultado

RANGO_MANUAL = "Rango manual"
TITULOS = {"comprobar": "Comprobar", "estado": "Estado",
           "bloquear": "Bloquear", "desbloquear": "Desbloquear"}


class Aplicacion:
    def __init__(self, raiz: tk.Tk, cfg: Config):
        self.raiz, self.cfg = raiz, cfg
        self.cola: "queue.Queue" = queue.Queue()
        self.ultima: Optional[tuple] = None  # (acción, objetivo, horas, resultados)
        raiz.title("Control de Examen")
        raiz.minsize(640, 420)

        datos = ttk.Frame(raiz, padding=8)
        datos.pack(fill="x")
        self.aula = tk.StringVar(value=RANGO_MANUAL)
        self.inicio, self.fin, self.profesor = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.horas = tk.StringVar(value=f"{cfg.horas:g}")

        ttk.Label(datos, text="Aula").grid(row=0, column=0, sticky="w")
        combo = self.combo = ttk.Combobox(datos, textvariable=self.aula, state="readonly",
                             values=[RANGO_MANUAL] + [a.nombre for a in cfg.aulas.values()])
        combo.grid(row=0, column=1, sticky="ew", padx=4)
        combo.bind("<<ComboboxSelected>>", self._al_elegir_aula)
        ttk.Label(datos, text="Autodesbloqueo (h)").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(datos, textvariable=self.horas, from_=0.5, to=config.MAX_HORAS,
                    increment=0.5, width=6).grid(row=0, column=3, sticky="w", padx=4)
        for fila, (texto, var) in enumerate([("IP inicial", self.inicio), ("IP final", self.fin),
                                             ("IP profesor", self.profesor)], start=1):
            ttk.Label(datos, text=texto).grid(row=fila, column=0, sticky="w")
            ttk.Entry(datos, textvariable=var).grid(row=fila, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(datos, text="(vacío: la del aula o .253)").grid(row=3, column=2, columnspan=2, sticky="w")
        datos.columnconfigure(1, weight=1)

        botones = ttk.Frame(raiz, padding=(8, 0))
        botones.pack(fill="x")
        self.botones = []
        for accion in ("comprobar", "estado", "bloquear", "desbloquear"):
            b = ttk.Button(botones, text=TITULOS[accion], command=lambda a=accion: self._lanzar(a))
            b.pack(side="left", padx=2)
            self.botones.append(b)
        b = ttk.Button(botones, text="Configuración…", command=self._configurar)
        b.pack(side="right", padx=2)
        self.botones.append(b)
        b = ttk.Button(botones, text="Reintentar fallidos", command=self._reintentar)
        b.pack(side="right", padx=2)
        self.botones.append(b)

        tabla = ttk.Frame(raiz, padding=8)
        tabla.pack(fill="both", expand=True)
        self.arbol = ttk.Treeview(tabla, columns=("ip", "resultado", "estado", "mensaje"), show="headings")
        for col, texto, ancho in [("ip", "Equipo", 120), ("resultado", "Resultado", 80),
                                  ("estado", "Estado", 180), ("mensaje", "Detalle", 300)]:
            self.arbol.heading(col, text=texto)
            self.arbol.column(col, width=ancho, stretch=(col == "mensaje"))
        self.arbol.tag_configure("ok", foreground="#1b7a2f")
        self.arbol.tag_configure("fallo", foreground="#b3261e")
        self.arbol.tag_configure("pendiente", foreground="#777777")
        barra = ttk.Scrollbar(tabla, orient="vertical", command=self.arbol.yview)
        self.arbol.configure(yscrollcommand=barra.set)
        self.arbol.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        self.aviso = tk.StringVar(value="Elige un aula o escribe un rango.")
        ttk.Label(raiz, textvariable=self.aviso, padding=8).pack(fill="x")
        raiz.after(100, self._vaciar_cola)

    def _configurar(self) -> None:
        Editor(self.raiz, self.cfg, self._config_guardada)

    def _config_guardada(self, cfg: Config) -> None:
        self.cfg = cfg
        nombres = [a.nombre for a in cfg.aulas.values()]
        self.combo.configure(values=[RANGO_MANUAL] + nombres)
        if self.aula.get() not in nombres:
            self.aula.set(RANGO_MANUAL)
        else:
            self._al_elegir_aula()
        self.aviso.set("Configuración guardada.")

    def _al_elegir_aula(self, _evento=None) -> None:
        nombre = self.aula.get()
        if nombre == RANGO_MANUAL:
            return
        aula = self.cfg.buscar_aula(nombre)
        self.inicio.set(aula.inicio)
        self.fin.set(aula.fin)
        self.profesor.set(aula.profesor)

    def _objetivo(self) -> Objetivo:
        aula = None if self.aula.get() == RANGO_MANUAL else self.aula.get()
        return config.resolver_objetivo(self.cfg, aula, self.inicio.get(),
                                        self.fin.get(), self.profesor.get())

    def _horas(self) -> float:
        try:
            return config.validar_horas(float(self.horas.get().replace(",", ".")))
        except ValueError:
            raise ErrorConfig(f"Horas de autodesbloqueo no válidas: '{self.horas.get()}'") from None

    def _lanzar(self, accion: str, objetivo: Optional[Objetivo] = None) -> None:
        try:
            objetivo = objetivo or self._objetivo()
            horas = self._horas() if accion == "bloquear" else 0
        except ErrorConfig as e:
            messagebox.showerror("Datos no válidos", str(e), parent=self.raiz)
            return

        clave = None
        if accion in ("bloquear", "desbloquear"):
            texto = f"¿{TITULOS[accion]} {objetivo.descripcion}?"
            if accion == "bloquear":
                texto += (f"\n\nSalidas permitidas: profesor {objetivo.profesor}, "
                          f"{', '.join(objetivo.permitidas)}\n"
                          f"Entradas permitidas: {', '.join(objetivo.puertos)}\n"
                          f"Autodesbloqueo en {horas:g} h")
                locales = config.ips_locales()
                if locales and objetivo.profesor not in locales:
                    texto += f"\n\nAVISO: este PC no tiene la IP {objetivo.profesor}."
            if not messagebox.askyesno(TITULOS[accion], texto, parent=self.raiz):
                return
            clave = simpledialog.askstring(
                "Contraseña", f"Contraseña de sudo de {self.cfg.usuario} en los alumnos:",
                show="*", parent=self.raiz)
            if not clave:
                return

        self.arbol.delete(*self.arbol.get_children())
        for ip in objetivo.ips:
            self.arbol.insert("", "end", iid=ip, values=(ip, "…", "", ""), tags=("pendiente",))
        self._ocupado(True)
        self.aviso.set(f"{TITULOS[accion]}: {objetivo.descripcion}…")
        threading.Thread(target=self._trabajo, args=(accion, objetivo, horas, clave),
                         daemon=True).start()

    def _trabajo(self, accion: str, objetivo: Objetivo, horas: float, clave: Optional[str]) -> None:
        avisar = lambda r: self.cola.put(("equipo", r))
        try:
            if accion == "comprobar":
                resultados = acciones.comprobar(objetivo.ips, self.cfg, avisar)
            elif accion == "estado":
                resultados = acciones.estado(objetivo.ips, self.cfg, avisar)
            elif accion == "bloquear":
                resultados = acciones.bloquear(objetivo.ips, self.cfg, clave, objetivo, horas, avisar)
            else:
                resultados = acciones.desbloquear(objetivo.ips, self.cfg, clave, avisar)
            acciones.registrar(accion, objetivo, resultados)
            self.cola.put(("fin", (accion, objetivo, horas, resultados)))
        except Exception as e:  # que la ventana nunca se quede bloqueada
            self.cola.put(("error", str(e)))

    def _vaciar_cola(self) -> None:
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "equipo":
                    self._mostrar(dato)
                elif tipo == "fin":
                    self._terminar(dato)
                else:
                    self._ocupado(False)
                    messagebox.showerror("Error", dato, parent=self.raiz)
        except queue.Empty:
            pass
        self.raiz.after(100, self._vaciar_cola)

    def _mostrar(self, r: Resultado) -> None:
        estado = r.estado + (f" hasta {r.hasta}" if r.hasta else "")
        self.arbol.item(r.ip, values=(r.ip, "OK" if r.ok else "FALLO", estado, r.mensaje),
                        tags=("ok" if r.ok else "fallo",))

    def _terminar(self, ultima: tuple) -> None:
        self.ultima = ultima
        resultados: List[Resultado] = ultima[3]
        fallos = sum(not r.ok for r in resultados)
        self.aviso.set(f"{TITULOS[ultima[0]]}: {len(resultados) - fallos} OK, {fallos} con fallo.")
        self._ocupado(False)

    def _reintentar(self) -> None:
        if not self.ultima:
            return
        accion, objetivo, _horas, resultados = self.ultima
        fallidos = [r.ip for r in resultados if not r.ok]
        if not fallidos:
            messagebox.showinfo("Reintentar", "No hay equipos con fallo.", parent=self.raiz)
            return
        self._lanzar(accion, replace(objetivo, ips=fallidos,
                                     descripcion=f"{len(fallidos)} equipos con fallo"))

    def _ocupado(self, ocupado: bool) -> None:
        for b in self.botones:
            b.state(["disabled"] if ocupado else ["!disabled"])


def main() -> int:
    raiz = tk.Tk()
    try:
        cfg = config.cargar()
    except ErrorConfig as e:
        raiz.withdraw()
        messagebox.showerror("Configuración no válida", str(e))
        return 2
    Aplicacion(raiz, cfg)
    raiz.mainloop()
    return 0
