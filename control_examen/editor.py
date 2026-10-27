"""Editor visual de aulas.ini (ventana Tkinter)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Dict, Optional

from . import config
from .config import Aula, Config, ErrorConfig

CAMPOS_AULA = ("nombre", "inicio", "fin", "profesor", "permitidas", "puertos")


class Editor:
    """Diálogo que edita la configuración. Al guardar llama a `al_guardar(cfg)`."""

    def __init__(self, raiz: tk.Misc, cfg: Config, al_guardar: Callable[[Config], None]):
        self.cfg, self.al_guardar = cfg, al_guardar
        self.ventana = tk.Toplevel(raiz)
        self.ventana.title("Configuración (aulas.ini)")
        self.ventana.minsize(680, 460)
        self.ventana.transient(raiz)

        # Borrador con los textos tal cual se escriben; se valida al guardar.
        self.general = {
            "usuario": tk.StringVar(value=cfg.usuario),
            "horas": tk.StringVar(value=f"{cfg.horas:g}"),
            "paralelo": tk.StringVar(value=str(cfg.paralelo)),
            "permitidas": tk.StringVar(value=", ".join(cfg.permitidas)),
            "puertos": tk.StringVar(value=", ".join(cfg.puertos)),
        }
        self.aulas: Dict[str, Dict[str, str]] = {
            a.clave: {
                "nombre": a.nombre, "inicio": a.inicio, "fin": a.fin, "profesor": a.profesor,
                "propias": a.permitidas is not None or a.puertos is not None,
                "permitidas": ", ".join(a.permitidas if a.permitidas is not None else cfg.permitidas),
                "puertos": ", ".join(a.puertos if a.puertos is not None else cfg.puertos),
            } for a in cfg.aulas.values()}
        self.actual: Optional[str] = None
        self.var = {c: tk.StringVar() for c in CAMPOS_AULA}
        self.propias = tk.BooleanVar()

        cuaderno = ttk.Notebook(self.ventana)
        cuaderno.pack(fill="both", expand=True, padx=8, pady=8)
        self._pestana_general(cuaderno)
        self._pestana_aulas(cuaderno)

        pie = ttk.Frame(self.ventana, padding=(8, 0, 8, 8))
        pie.pack(fill="x")
        ttk.Label(pie, text=str(config.ruta_config()), foreground="#777777").pack(side="left")
        ttk.Button(pie, text="Cancelar", command=self.ventana.destroy).pack(side="right", padx=2)
        ttk.Button(pie, text="Guardar", command=self._guardar).pack(side="right", padx=2)

        if self.aulas:
            self.lista.selection_set(0)
            self._seleccionar()
        else:
            self._habilitar(False)
        self.ventana.grab_set()

    # --- pestañas ---------------------------------------------------------
    def _pestana_general(self, cuaderno: ttk.Notebook) -> None:
        marco = ttk.Frame(cuaderno, padding=12)
        cuaderno.add(marco, text="General")
        filas = [
            ("Usuario SSH", "usuario", "Usuario con el que se entra en los alumnos."),
            ("Autodesbloqueo (h)", "horas", f"El bloqueo se retira solo pasado este tiempo (máx. {config.MAX_HORAS})."),
            ("Equipos en paralelo", "paralelo", "Entre 1 y 100."),
            ("Salidas permitidas", "permitidas", "IP, IP/prefijo, IP:puerto[/tcp|udp], separadas por comas."),
            ("Puertos de entrada", "puertos", "puerto[/tcp|udp], separados por comas. El 22/tcp se añade siempre."),
        ]
        for i, (texto, clave, ayuda) in enumerate(filas):
            ttk.Label(marco, text=texto).grid(row=2 * i, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(marco, textvariable=self.general[clave]).grid(row=2 * i, column=1, sticky="ew", padx=6, pady=(6, 0))
            ttk.Label(marco, text=ayuda, foreground="#777777").grid(row=2 * i + 1, column=1, sticky="w", padx=6)
        marco.columnconfigure(1, weight=1)

    def _pestana_aulas(self, cuaderno: ttk.Notebook) -> None:
        marco = ttk.Frame(cuaderno, padding=12)
        cuaderno.add(marco, text="Aulas")

        izq = ttk.Frame(marco)
        izq.pack(side="left", fill="y")
        self.lista = tk.Listbox(izq, exportselection=False, width=18, height=12)
        self.lista.pack(fill="y", expand=True)
        for clave in self.aulas:
            self.lista.insert("end", clave)
        self.lista.bind("<<ListboxSelect>>", lambda _e: self._seleccionar())
        botones = ttk.Frame(izq)
        botones.pack(fill="x", pady=(6, 0))
        ttk.Button(botones, text="Añadir", command=self._añadir).pack(side="left", expand=True, fill="x")
        ttk.Button(botones, text="Quitar", command=self._quitar).pack(side="left", expand=True, fill="x")

        der = ttk.Frame(marco, padding=(14, 0, 0, 0))
        der.pack(side="left", fill="both", expand=True)
        self.entradas = []
        for i, (texto, clave) in enumerate([("Nombre", "nombre"), ("IP inicial", "inicio"),
                                            ("IP final", "fin"), ("IP profesor", "profesor")]):
            ttk.Label(der, text=texto).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(der, textvariable=self.var[clave])
            e.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            self.entradas.append(e)
        self.casilla = ttk.Checkbutton(der, text="Reglas propias (sustituyen a las de General)",
                                       variable=self.propias, command=self._al_cambiar_propias)
        self.casilla.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 2))
        self.reglas = []
        for i, (texto, clave) in enumerate([("Salidas permitidas", "permitidas"),
                                            ("Puertos de entrada", "puertos")], start=5):
            ttk.Label(der, text=texto).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(der, textvariable=self.var[clave])
            e.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            self.reglas.append(e)
        ttk.Label(der, text="Si no hay reglas propias, se usan las de General.",
                  foreground="#777777").grid(row=7, column=1, sticky="w", padx=6)
        der.columnconfigure(1, weight=1)

    # --- aulas ------------------------------------------------------------
    def _habilitar(self, si: bool) -> None:
        for w in self.entradas + [self.casilla]:
            w.state(["!disabled"] if si else ["disabled"])
        self._al_cambiar_propias()

    def _al_cambiar_propias(self) -> None:
        activo = self.actual is not None and self.propias.get()
        for w in self.reglas:
            w.state(["!disabled"] if activo else ["disabled"])

    def _volcar(self) -> None:
        """Guarda el formulario en el borrador del aula actual."""
        if self.actual is None:
            return
        datos = self.aulas[self.actual]
        for c in CAMPOS_AULA:
            datos[c] = self.var[c].get()
        datos["propias"] = self.propias.get()

    def _seleccionar(self) -> None:
        sel = self.lista.curselection()
        if not sel:
            return
        self._volcar()
        self.actual = self.lista.get(sel[0])
        datos = self.aulas[self.actual]
        for c in CAMPOS_AULA:
            self.var[c].set(datos[c])
        self.propias.set(datos["propias"])
        self._habilitar(True)

    def _añadir(self) -> None:
        clave = simpledialog.askstring("Nueva aula", "Clave del aula (por ejemplo aula15):",
                                       parent=self.ventana)
        if not clave:
            return
        try:
            clave = config.validar_clave_aula(clave)
        except ErrorConfig as e:
            messagebox.showerror("Aula", str(e), parent=self.ventana)
            return
        if clave in self.aulas:
            messagebox.showerror("Aula", f"Ya existe '{clave}'.", parent=self.ventana)
            return
        self._volcar()
        self.aulas[clave] = {
            "nombre": clave, "inicio": "", "fin": "", "profesor": "", "propias": False,
            "permitidas": self.general["permitidas"].get(), "puertos": self.general["puertos"].get()}
        self.lista.insert("end", clave)
        self.lista.selection_clear(0, "end")
        self.lista.selection_set("end")
        self._seleccionar()

    def _quitar(self) -> None:
        sel = self.lista.curselection()
        if not sel:
            return
        clave = self.lista.get(sel[0])
        if not messagebox.askyesno("Quitar aula", f"¿Quitar el aula '{clave}'?", parent=self.ventana):
            return
        del self.aulas[clave]
        self.lista.delete(sel[0])
        self.actual = None
        for c in CAMPOS_AULA:
            self.var[c].set("")
        self.propias.set(False)
        if self.aulas:
            self.lista.selection_set(0)
            self._seleccionar()
        else:
            self._habilitar(False)

    # --- guardar ----------------------------------------------------------
    def _construir(self) -> Config:
        g = self.general
        try:
            horas = config.validar_horas(float(g["horas"].get().replace(",", ".")))
            paralelo = int(g["paralelo"].get())
        except ValueError:
            raise ErrorConfig("General: horas y equipos en paralelo deben ser números") from None
        if not 1 <= paralelo <= 100:
            raise ErrorConfig("General: 'paralelo' debe estar entre 1 y 100")
        usuario = g["usuario"].get().strip()
        if not config.RE_USUARIO.match(usuario):
            raise ErrorConfig(f"General: usuario no válido: '{usuario}'")
        try:
            cfg = Config(usuario, config.lista_salidas(g["permitidas"].get()), horas,
                         config.lista_entradas(g["puertos"].get()), paralelo)
        except ErrorConfig as e:
            raise ErrorConfig(f"General: {e}") from None

        for clave, d in self.aulas.items():
            try:
                inicio, fin = config.validar_ip(d["inicio"]), config.validar_ip(d["fin"])
                config.expandir_rango(inicio, fin)
                profesor = (config.validar_ip(d["profesor"]) if d["profesor"].strip()
                            else config.profesor_por_defecto(inicio))
                permitidas = config.lista_salidas(d["permitidas"]) if d["propias"] else None
                puertos = config.lista_entradas(d["puertos"]) if d["propias"] else None
            except ErrorConfig as e:
                raise ErrorConfig(f"Aula {clave}: {e}") from None
            cfg.aulas[clave] = Aula(clave, d["nombre"].strip() or clave, inicio, fin, profesor,
                                    permitidas, puertos)
        return cfg

    def _guardar(self) -> None:
        self._volcar()
        try:
            cfg = self._construir()
            ruta = config.guardar(cfg)
        except (ErrorConfig, OSError) as e:
            messagebox.showerror("No se puede guardar", str(e), parent=self.ventana)
            return
        self.al_guardar(cfg)
        messagebox.showinfo("Guardado", f"Configuración guardada en {ruta}", parent=self.ventana)
        self.ventana.destroy()
