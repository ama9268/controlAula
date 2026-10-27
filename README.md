# Control de Examen

Bloquea la red de los PCs de los alumnos (Ubuntu) durante un examen y la restaura después.
Se ejecuta en el PC del profesor y actúa por SSH sobre un rango de IPs o un aula, **en paralelo**.

Con el bloqueo activo, cada PC de alumno:

- **solo puede salir** hacia el PC del profesor y los destinos que configures (por defecto Moodle x.x.x.4 y el servidor web x.x.x.2), además de DHCP;
- **solo acepta entradas** por los puertos que configures (por defecto SSH 22 y Veyon 11100);
- **pierde las conexiones salientes que ya tuviera abiertas** (pestañas, chats…);
- **sigue bloqueado aunque se reinicie**;
- **se desbloquea solo** pasadas las horas indicadas (4 por defecto), por si el profesor lo olvida o su PC falla.

El bloqueo usa una tabla nftables propia (`inet examen`), independiente de ufw. No modifica ni borra la configuración de ufw de los equipos.

## Requisitos

- **PC del profesor**: Ubuntu con `python3` (versión 3.8 o superior) y `sudo apt install python3-tk openssh-client`.
- **PCs de los alumnos**: Ubuntu 20.04 o posterior (incluye `nft`). El usuario del profesor debe poder entrar con clave SSH, igual que con `cssh`, y tener permisos de sudo.

No hace falta instalar nada en los alumnos. El agente (`examen-fw`) se envía y se comprueba en cada operación.

## Instalación

```bash
mkdir -p ~/.config/control-examen
cp aulas.ini.ejemplo ~/.config/control-examen/aulas.ini   # y editar las aulas
./control-examen comprobar --aula aula14                  # ensayo, sin sudo
```

## Configurar salidas y entradas

Todo se define en `aulas.ini`, en `[general]` o en cada aula.

| Clave | Qué controla | Formatos |
|---|---|---|
| `permitidas` | Destinos a los que pueden salir los alumnos (además del PC del profesor, que siempre se permite) | `IP`, `IP/prefijo`, `IP:puerto`, `IP:puerto/tcp\|udp` |
| `puertos_entrada` | Puertos por los que pueden recibir conexiones | `puerto`, `puerto/tcp\|udp` |

Sin protocolo se entiende `tcp`. Ejemplos: `permitidas = 10.0.0.4, 10.0.5.0/24, 10.0.0.7:443, 10.0.0.9:53/udp` y `puertos_entrada = 22, 11100, 5900/udp`.

- `22/tcp` (SSH) se añade siempre a las entradas: sin él se perdería el control del equipo. El agente también se niega a bloquear si no está.
- Si un aula define `permitidas` o `puertos_entrada`, **sustituyen** a los de `[general]` solo en esa aula. Hay que repetir lo que se quiera mantener (por ejemplo, Moodle).
- Una regla con puerto (`10.0.0.2:443`) solo abre ese puerto en ese destino; sin puerto (`10.0.0.2`) se abre todo el tráfico a esa IP.
- Solo IPv4 en las salidas. Cualquier valor que no cumpla el formato se rechaza al cargar el fichero y, de nuevo, en el equipo del alumno.

## Uso

Sin argumentos abre la ventana: se elige el aula (o un rango), se pulsa **Bloquear** o **Desbloquear** y se introduce la contraseña de sudo. La tabla muestra el resultado de cada equipo y **Reintentar fallidos** repite la operación solo sobre los que fallaron.

Desde la terminal:

```bash
./control-examen aulas
./control-examen comprobar   --aula aula14          # acceso SSH y nftables (sin sudo)
./control-examen bloquear    --aula aula14 --horas 2
./control-examen estado      --aula aula14          # sin sudo
./control-examen desbloquear --aula aula14
./control-examen bloquear    --inicio 10.14.14.20 --fin 10.14.14.20   # un solo equipo
```

`--profesor IP` cambia la IP del profesor que se permite. Por defecto se usa la del aula o, si no está definida, la `.253` de la red. `-s` evita la pregunta de confirmación. El código de salida es distinto de 0 si algún equipo falla.

Las acciones quedan registradas en `~/.local/state/control-examen/registro.log` (sin contraseñas).

### Si algo va mal en un PC de alumno

Entrar por SSH, por ejemplo con `cssh`, y ejecutar `sudo /usr/local/sbin/examen-fw desbloquear`. Como último recurso, `sudo nft delete table inet examen && sudo systemctl disable examen-fw.service` (durante el bloqueo SSH sigue permitido).

## Seguridad

| Riesgo | Medida |
|---|---|
| Fuga de la contraseña de sudo | Se pide una vez por operación con un campo oculto. Viaja solo por la entrada estándar del SSH cifrado, nunca en la línea de órdenes (visible con `ps`), variables de entorno, ficheros ni registro. Se elimina de cualquier salida mostrada. |
| Inyección de órdenes | `subprocess` sin shell. IPs, puertos y horas se validan en Python (`ipaddress`) y de nuevo en el agente con expresiones estrictas antes de escribir las reglas. Los argumentos remotos se citan con `shlex.quote`. |
| Agente manipulado | Se instala como `root:root 0755` desde un temporal de `mktemp` y se comprueba su SHA-256 antes de ejecutarlo como root. |
| Suplantación de un equipo | `StrictHostKeyChecking=accept-new`: si cambia la clave de host conocida, ese equipo se marca como FALLO. `BatchMode` evita esperar contraseñas. No se reenvían ni el agente SSH ni X11. |
| El alumno se salta el bloqueo | Política *drop* en entrada y salida (IPv4 e IPv6). Persiste tras reiniciar. Sin sudo no se pueden tocar las reglas, los ficheros de `/etc/examen-fw` ni las unidades systemd. |
| Perder el control del equipo | SSH siempre permitido (si falta, el agente se niega a bloquear). Las reglas se validan con `nft -c` antes de aplicarlas. Hay autodesbloqueo por tiempo. Nunca se actúa sobre la IP del propio profesor. |

Limitaciones conocidas:

- Un alumno con sudo en su PC, o que arranque desde un USB, puede saltarse el bloqueo. Esto queda fuera del alcance de la herramienta.
- No hay DNS durante el examen, así que Moodle y el servidor web solo funcionan por IP. Si Moodle redirige a un nombre de dominio, fallará.
- La contraseña es una cadena de Python y no se puede borrar de memoria de forma garantizada. Solo vive mientras dura la operación.
- En el primer contacto con un equipo, `accept-new` acepta su clave de host. Los equipos ya usados con `cssh` están en `known_hosts`.

## Desarrollo

```bash
python3 -m unittest        # configuración, comandos remotos y validación del agente
bash -n control_examen/agente/examen-fw.sh && shellcheck control_examen/agente/examen-fw.sh
bash control_examen/agente/examen-fw.sh generar-nft --permitidas x.x.x.253,x.x.x.4,x.x.x.7:443 --puertos 22,11100,5900/udp
```

Antes de usarlo en un examen real, pruébalo en un solo equipo:

1. Bloquear y comprobar desde el alumno que `curl https://www.google.com` falla y que `curl http://x.x.x.4` funciona.
2. Comprobar que el SSH y Veyon del profesor siguen funcionando.
3. Reiniciar el alumno y comprobar que sigue bloqueado.
4. Probar `--horas 0.05` y comprobar que se libera solo a los 3 minutos.
5. Desbloquear y comprobar que `ufw status` queda igual que antes.
